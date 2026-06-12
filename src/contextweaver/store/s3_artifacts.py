"""S3-compatible artifact store for contextweaver (issue #426).

A persistent :class:`~contextweaver.store.protocols.ArtifactStore` over the S3
API, so firewalled artifacts can outlive a single process or host.  Works with
AWS S3 and S3-compatible services (MinIO, Cloudflare R2, GCS interop) — point it
at an ``endpoint_url`` and bucket.

Each artifact occupies two objects: ``{prefix}/{handle}.data`` (raw bytes) and
``{prefix}/{handle}.json`` (the :class:`~contextweaver.types.ArtifactRef`
metadata).  ``boto3`` is imported lazily (``pip install 'contextweaver[s3]'``).
Conformance-tested against ``moto`` in CI; works unchanged against a real
endpoint.  Pass either a boto3 S3 ``client`` or a ``bucket`` (+ optional
``endpoint_url`` / ``region_name``).
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from contextweaver.exceptions import ArtifactNotFoundError, ConfigError
from contextweaver.store.artifacts import _apply_selector
from contextweaver.types import ArtifactRef

logger = logging.getLogger("contextweaver.store")

_DATA_SUFFIX = ".data"
_META_SUFFIX = ".json"
_NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NoSuchBucket"})


def _require_boto3() -> Any:  # noqa: ANN401 - returns the untyped ``boto3`` module
    """Import and return the ``boto3`` module, or raise a clear :class:`ConfigError`."""
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ConfigError(
            "S3ArtifactStore requires the 's3' extra: pip install 'contextweaver[s3]'"
        ) from exc
    return boto3


class S3ArtifactStore:
    """S3-compatible implementation of the :class:`ArtifactStore` protocol.

    Args:
        bucket: Target bucket name.
        client: A pre-built boto3 S3 client.  When omitted, one is created from
            *endpoint_url* / *region_name* (and ambient AWS credentials).
        prefix: Key prefix under which artifacts are stored (default
            ``"artifacts"``).
        endpoint_url: Optional S3-compatible endpoint (MinIO/R2/GCS).
        region_name: Optional AWS region.
    """

    def __init__(
        self,
        bucket: str,
        *,
        client: Any | None = None,  # noqa: ANN401 - boto3 S3 client has no public type
        prefix: str = "artifacts",
        endpoint_url: str | None = None,
        region_name: str | None = None,
    ) -> None:
        if not bucket:
            raise ConfigError("S3ArtifactStore requires a non-empty bucket name")
        if client is None:
            client = _require_boto3().client(
                "s3", endpoint_url=endpoint_url, region_name=region_name
            )
        self._client = client
        self._bucket = bucket
        self._key_prefix = f"{prefix.rstrip('/')}/" if prefix else ""
        self._client_error = client.exceptions.ClientError

    def _data_key(self, handle: str) -> str:
        return f"{self._key_prefix}{handle}{_DATA_SUFFIX}"

    def _meta_key(self, handle: str) -> str:
        return f"{self._key_prefix}{handle}{_META_SUFFIX}"

    def _is_not_found(self, exc: Exception) -> bool:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
        return code in _NOT_FOUND_CODES

    def put(
        self,
        handle: str,
        content: bytes,
        media_type: str = "application/octet-stream",
        label: str = "",
    ) -> ArtifactRef:
        """Store *content* under *handle* and return its :class:`ArtifactRef`.

        The returned ref carries a sha256 ``content_hash`` (firewall #190).
        """
        ref = ArtifactRef(
            handle=handle,
            media_type=media_type,
            size_bytes=len(content),
            label=label,
            content_hash=hashlib.sha256(content).hexdigest(),
        )
        self._client.put_object(
            Bucket=self._bucket,
            Key=self._data_key(handle),
            Body=content,
            ContentType=media_type,
        )
        self._client.put_object(
            Bucket=self._bucket,
            Key=self._meta_key(handle),
            Body=json.dumps(ref.to_dict(), sort_keys=True).encode("utf-8"),
            ContentType="application/json",
        )
        logger.debug("s3_artifacts.put: handle=%s, size=%d", handle, len(content))
        return ref

    def get(self, handle: str) -> bytes:
        """Retrieve the raw bytes for *handle*.

        Raises:
            ArtifactNotFoundError: If *handle* is not in the store.
        """
        try:
            obj = self._client.get_object(Bucket=self._bucket, Key=self._data_key(handle))
        except self._client_error as exc:
            if self._is_not_found(exc):
                raise ArtifactNotFoundError(f"Artifact not found: {handle!r}") from exc
            raise
        body: bytes = obj["Body"].read()
        return body

    def ref(self, handle: str) -> ArtifactRef:
        """Return the :class:`ArtifactRef` metadata for *handle*.

        Raises:
            ArtifactNotFoundError: If *handle* is not in the store.
        """
        try:
            obj = self._client.get_object(Bucket=self._bucket, Key=self._meta_key(handle))
        except self._client_error as exc:
            if self._is_not_found(exc):
                raise ArtifactNotFoundError(f"Artifact not found: {handle!r}") from exc
            raise
        return ArtifactRef.from_dict(json.loads(obj["Body"].read()))

    def list_refs(self) -> list[ArtifactRef]:
        """Return all stored :class:`ArtifactRef` objects, sorted by handle."""
        paginator = self._client.get_paginator("list_objects_v2")
        refs: list[ArtifactRef] = []
        for page in paginator.paginate(Bucket=self._bucket, Prefix=self._key_prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.endswith(_META_SUFFIX):
                    continue
                meta = self._client.get_object(Bucket=self._bucket, Key=key)
                refs.append(ArtifactRef.from_dict(json.loads(meta["Body"].read())))
        return sorted(refs, key=lambda r: r.handle)

    def delete(self, handle: str) -> None:
        """Remove the artifact identified by *handle*.

        Raises:
            ArtifactNotFoundError: If *handle* is not in the store.
        """
        if not self.exists(handle):
            raise ArtifactNotFoundError(f"Artifact not found: {handle!r}")
        self._client.delete_object(Bucket=self._bucket, Key=self._data_key(handle))
        self._client.delete_object(Bucket=self._bucket, Key=self._meta_key(handle))

    def exists(self, handle: str) -> bool:
        """Return ``True`` if *handle* is in the store."""
        try:
            self._client.head_object(Bucket=self._bucket, Key=self._data_key(handle))
        except self._client_error as exc:
            if self._is_not_found(exc):
                return False
            raise
        return True

    def metadata(self, handle: str) -> ArtifactRef:
        """Return the :class:`ArtifactRef` for *handle* (alias for :meth:`ref`)."""
        return self.ref(handle)

    def drilldown(self, handle: str, selector: dict[str, Any]) -> str:
        """Return a subset of the artifact's content according to *selector*.

        Uses the same selector dialects as the other backends.

        Raises:
            ArtifactNotFoundError: If *handle* is not in the store.
            ContextWeaverError: If the selector type is unknown.
        """
        raw = self.get(handle).decode("utf-8", errors="replace")
        return _apply_selector(raw, selector)

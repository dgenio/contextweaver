"""Redis-backed artifact store for contextweaver (issue #426).

A persistent :class:`~contextweaver.store.protocols.ArtifactStore` for
multi-process / long-lived gateways, where in-process or single-file backends
are not enough.  Raw bytes and the :class:`~contextweaver.types.ArtifactRef`
metadata live under namespaced Redis keys; an optional TTL expires artifacts
automatically.

``redis`` is imported lazily, so importing this module never requires the
dependency — only constructing a store without passing a client does.  Install
it with ``pip install 'contextweaver[redis]'``.

The store is conformance-tested (`tests/test_store_redis.py`) against
``fakeredis`` in CI and works unchanged against a real server; pass either a
``redis.Redis`` client or a ``url``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

from contextweaver.exceptions import ArtifactNotFoundError, ConfigError
from contextweaver.store.artifacts import _apply_selector
from contextweaver.types import ArtifactRef

if TYPE_CHECKING:
    import redis

logger = logging.getLogger("contextweaver.store")


def _require_redis() -> Any:  # noqa: ANN401 - returns the untyped ``redis`` module
    """Import and return the ``redis`` module, or raise a clear :class:`ConfigError`."""
    try:
        import redis as redis_mod
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ConfigError(
            "RedisArtifactStore requires the 'redis' extra: pip install 'contextweaver[redis]'"
        ) from exc
    return redis_mod


class RedisArtifactStore:
    """Redis implementation of the :class:`ArtifactStore` protocol.

    Args:
        client: A pre-configured ``redis.Redis`` client.  Mutually exclusive
            with *url*; one of the two is required.
        url: A Redis connection URL (e.g. ``redis://localhost:6379/0``) used to
            build a client when *client* is not given.
        namespace: Key prefix isolating this store's keys (default ``"cw"``).
        ttl_seconds: Optional per-artifact TTL; ``None`` (default) never expires.

    Keys: ``{namespace}:art:data:{handle}`` (raw bytes) and
    ``{namespace}:art:meta:{handle}`` (JSON :class:`ArtifactRef`).  ``list_refs``
    scans the metadata keys, so a TTL-expired artifact simply drops out of the
    listing without leaving a dangling index entry.
    """

    def __init__(
        self,
        client: redis.Redis | None = None,
        *,
        url: str | None = None,
        namespace: str = "cw",
        ttl_seconds: int | None = None,
    ) -> None:
        if client is None:
            if url is None:
                raise ConfigError("RedisArtifactStore requires either a client or a url")
            client = _require_redis().Redis.from_url(url)
        self._client = client
        self._ttl = ttl_seconds
        self._data_prefix = f"{namespace}:art:data:"
        self._meta_prefix = f"{namespace}:art:meta:"

    def _data_key(self, handle: str) -> str:
        return f"{self._data_prefix}{handle}"

    def _meta_key(self, handle: str) -> str:
        return f"{self._meta_prefix}{handle}"

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
        meta = json.dumps(ref.to_dict(), sort_keys=True).encode("utf-8")
        pipe = self._client.pipeline()
        pipe.set(self._data_key(handle), content)
        pipe.set(self._meta_key(handle), meta)
        if self._ttl is not None:
            pipe.expire(self._data_key(handle), self._ttl)
            pipe.expire(self._meta_key(handle), self._ttl)
        pipe.execute()
        logger.debug("redis_artifacts.put: handle=%s, size=%d", handle, len(content))
        return ref

    def get(self, handle: str) -> bytes:
        """Retrieve the raw bytes for *handle*.

        Raises:
            ArtifactNotFoundError: If *handle* is not in the store.
        """
        data = self._client.get(self._data_key(handle))
        if data is None:
            raise ArtifactNotFoundError(f"Artifact not found: {handle!r}")
        # redis-py's ``get`` is typed ``bytes | str | Any`` (a client configured
        # with ``decode_responses=True`` returns ``str``); normalise to ``bytes``
        # so the artifact bytes round-trip regardless of client decode settings.
        return data.encode() if isinstance(data, str) else bytes(data)

    def ref(self, handle: str) -> ArtifactRef:
        """Return the :class:`ArtifactRef` metadata for *handle*.

        Raises:
            ArtifactNotFoundError: If *handle* is not in the store.
        """
        meta = self._client.get(self._meta_key(handle))
        if meta is None:
            raise ArtifactNotFoundError(f"Artifact not found: {handle!r}")
        return ArtifactRef.from_dict(json.loads(meta))

    def list_refs(self) -> list[ArtifactRef]:
        """Return all stored :class:`ArtifactRef` objects, sorted by handle."""
        refs: list[ArtifactRef] = []
        for key in self._client.scan_iter(match=f"{self._meta_prefix}*"):
            meta = self._client.get(key)
            if meta is not None:
                refs.append(ArtifactRef.from_dict(json.loads(meta)))
        return sorted(refs, key=lambda r: r.handle)

    def delete(self, handle: str) -> None:
        """Remove the artifact identified by *handle*.

        Raises:
            ArtifactNotFoundError: If *handle* is not in the store.
        """
        removed = self._client.delete(self._data_key(handle), self._meta_key(handle))
        if not removed:
            raise ArtifactNotFoundError(f"Artifact not found: {handle!r}")

    def exists(self, handle: str) -> bool:
        """Return ``True`` if *handle* is in the store."""
        return bool(self._client.exists(self._data_key(handle)))

    def metadata(self, handle: str) -> ArtifactRef:
        """Return the :class:`ArtifactRef` for *handle* (alias for :meth:`ref`)."""
        return self.ref(handle)

    def drilldown(self, handle: str, selector: dict[str, Any]) -> str:
        """Return a subset of the artifact's content according to *selector*.

        Uses the same selector dialects as the other backends (``head``,
        ``lines``, ``json_keys``, ``rows``).

        Raises:
            ArtifactNotFoundError: If *handle* is not in the store.
            ContextWeaverError: If the selector type is unknown.
        """
        raw = self.get(handle).decode("utf-8", errors="replace")
        return _apply_selector(raw, selector)

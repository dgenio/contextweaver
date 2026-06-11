"""Filesystem-backed artifact store for contextweaver.

A persistent :class:`~contextweaver.store.protocols.ArtifactStore`
implementation that mirrors :class:`~contextweaver.store.artifacts.InMemoryArtifactStore`
on disk: each artifact is stored as a ``{handle}.data`` file (raw bytes)
paired with a ``{handle}.json`` file (the :class:`~contextweaver.types.ArtifactRef`
metadata, JSON-encoded).

Useful when artifacts are large (full API responses, images) and the agent
wants directly-inspectable, human-grep-friendly files.

Durability and limits (issue #497): writes are **atomic** (temp file +
:func:`os.replace`, so a crash never leaves a truncated file); an in-memory
handle -> :class:`ArtifactRef` index is built once on construction and
maintained on ``put`` / ``delete``, so :meth:`list_refs` never rescans the
directory; and optional ``max_bytes`` / ``max_artifacts`` quotas raise
:class:`~contextweaver.exceptions.ArtifactStoreQuotaError`.

Concurrency (issue #458): single process only. Within one process ``put`` /
``delete`` / :meth:`list_refs` are serialised by an internal lock, so a shared
instance is thread-safe; there is no cross-process advisory locking.

Handle safety (issue #466): handles are validated (path separators, ``..``
traversal, and null bytes rejected) and then **percent-encoded** into
filenames, so a handle legal as a handle but hostile as a filename — chiefly
``:`` (which opens an NTFS alternate data stream on Windows; the firewall emits
``artifact:result:call_1``) — is stored portably. See
:mod:`contextweaver.store._json_file_io`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any

from contextweaver.exceptions import ArtifactNotFoundError, ArtifactStoreQuotaError
from contextweaver.store._json_file_io import DATA_SUFFIX as _DATA_SUFFIX
from contextweaver.store._json_file_io import META_SUFFIX as _META_SUFFIX
from contextweaver.store._json_file_io import atomic_write as _atomic_write
from contextweaver.store._json_file_io import consistent_data_size as _consistent_data_size
from contextweaver.store._json_file_io import encode_handle as _encode_handle
from contextweaver.store._json_file_io import validate_handle as _validate_handle
from contextweaver.store.artifacts import _apply_selector
from contextweaver.types import ArtifactRef

logger = logging.getLogger("contextweaver.store")


class JsonFileArtifactStore:
    """Filesystem implementation of the :class:`ArtifactStore` protocol.

    All artifacts are stored under *base_dir*.  Each artifact occupies two
    sibling files, named after the percent-encoded handle:

    - ``{base_dir}/{enc(handle)}.json`` — :class:`~contextweaver.types.ArtifactRef`
      metadata, serialised via :meth:`ArtifactRef.to_dict` + :func:`json.dumps`.
    - ``{base_dir}/{enc(handle)}.data`` — raw bytes.

    The directory is created on instantiation if absent, and existing
    ``*.json`` metadata files are scanned once into an in-memory index, so
    re-instantiating against an existing directory recovers the previous
    metadata index without rescanning on every :meth:`list_refs`.

    Args:
        base_dir: Directory that backs the store (created if missing).
        max_bytes: Optional ceiling on the total size of stored artifact
            bytes.  ``None`` (default) means unbounded.
        max_artifacts: Optional ceiling on the number of stored artifacts.
            ``None`` (default) means unbounded.
    """

    def __init__(
        self,
        base_dir: str | Path,
        *,
        max_bytes: int | None = None,
        max_artifacts: int | None = None,
    ) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._max_bytes = max_bytes
        self._max_artifacts = max_artifacts
        self._index: dict[str, ArtifactRef] = {}
        self._total_bytes = 0
        # Serialises put/delete/list_refs so a single-process gateway can share
        # one instance across threads without racing the index/byte counter.
        self._lock = threading.RLock()
        self._load_index()
        logger.debug(
            "json_file_artifacts.init: base_dir=%s, artifacts=%d, bytes=%d",
            self._base_dir,
            len(self._index),
            self._total_bytes,
        )

    @property
    def base_dir(self) -> Path:
        """Filesystem directory backing this store."""
        return self._base_dir

    # ------------------------------------------------------------------
    # Index
    # ------------------------------------------------------------------

    def _load_index(self) -> None:
        """Populate the in-memory index from the metadata files on disk.

        Runs once at construction.  An entry is indexed only when it is both
        decodable *and* self-consistent — a valid handle, a metadata filename
        that matches ``enc(handle).json``, and a present ``.data`` file (see
        :func:`~contextweaver.store._json_file_io.consistent_data_size`).
        Orphan or mismatched metadata is skipped (logged at ``DEBUG``) so the
        index never advertises a handle :meth:`get` cannot serve and the quota
        byte counter reflects bytes actually on disk (#497 review).
        """
        for meta in self._base_dir.glob(f"*{_META_SUFFIX}"):
            try:
                raw = json.loads(meta.read_text(encoding="utf-8"))
                ref = ArtifactRef.from_dict(raw)
            except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
                logger.debug("json_file_artifacts.load_index: skip %s (%s)", meta.name, exc)
                continue
            size = _consistent_data_size(self._base_dir, meta.name, ref)
            if size is None:
                logger.debug("json_file_artifacts.load_index: skip inconsistent %s", meta.name)
                continue
            self._index[ref.handle] = ref
            self._total_bytes += size

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _meta_path(self, handle: str) -> Path:
        # Centralised validation keeps every public method that resolves a
        # handle (put/get/ref/delete/exists/drilldown/metadata) safe against
        # path-traversal; encoding then makes the stem filename-safe (#466).
        _validate_handle(handle)
        return self._base_dir / f"{_encode_handle(handle)}{_META_SUFFIX}"

    def _data_path(self, handle: str) -> Path:
        _validate_handle(handle)
        return self._base_dir / f"{_encode_handle(handle)}{_DATA_SUFFIX}"

    # ------------------------------------------------------------------
    # Quota
    # ------------------------------------------------------------------

    def _check_quota(self, handle: str, new_size: int) -> None:
        """Raise :class:`ArtifactStoreQuotaError` if storing *new_size* breaks a limit."""
        existing = self._index.get(handle)
        if (
            self._max_artifacts is not None
            and existing is None
            and len(self._index) >= self._max_artifacts
        ):
            raise ArtifactStoreQuotaError(
                f"artifact count limit reached ({self._max_artifacts}); cannot store {handle!r}"
            )
        if self._max_bytes is not None:
            prospective = self._total_bytes - (existing.size_bytes if existing else 0) + new_size
            if prospective > self._max_bytes:
                raise ArtifactStoreQuotaError(
                    f"byte limit reached ({self._max_bytes}); "
                    f"storing {handle!r} ({new_size} bytes) would total {prospective}"
                )

    # ------------------------------------------------------------------
    # ArtifactStore protocol
    # ------------------------------------------------------------------

    def put(
        self,
        handle: str,
        content: bytes,
        media_type: str = "application/octet-stream",
        label: str = "",
    ) -> ArtifactRef:
        """Store *content* under *handle* and return its :class:`ArtifactRef`.

        The data and metadata files are each written atomically (temp file +
        :func:`os.replace`), so a crash never leaves a half-written pair.  The
        returned ref carries a populated ``content_hash`` (sha256 of *content*,
        #466), which is persisted with the metadata and powers the firewall's
        cross-restart idempotency short-circuit (#190).

        Raises:
            ContextWeaverError: If *handle* contains a path separator,
                ``..``, ``.``, or a null byte.
            ArtifactStoreQuotaError: If the write would exceed ``max_bytes``
                or ``max_artifacts``.
        """
        _validate_handle(handle)
        ref = ArtifactRef(
            handle=handle,
            media_type=media_type,
            size_bytes=len(content),
            label=label,
            content_hash=hashlib.sha256(content).hexdigest(),
        )
        with self._lock:
            self._check_quota(handle, len(content))
            # Data first, then metadata: a crash between the two leaves an
            # orphan ``.data`` file that no index entry references (harmless),
            # never a metadata file advertising bytes that are not there.
            _atomic_write(self._data_path(handle), content)
            _atomic_write(
                self._meta_path(handle),
                json.dumps(ref.to_dict(), sort_keys=True).encode("utf-8"),
            )
            previous = self._index.get(handle)
            self._total_bytes += len(content) - (previous.size_bytes if previous else 0)
            self._index[handle] = ref
        logger.debug("json_file_artifacts.put: handle=%s, size=%d", handle, len(content))
        return ref

    def get(self, handle: str) -> bytes:
        """Retrieve the raw bytes for *handle*.

        Raises:
            ArtifactNotFoundError: If *handle* is not in the store.
        """
        data_path = self._data_path(handle)
        if not data_path.is_file():
            raise ArtifactNotFoundError(f"Artifact not found: {handle!r}")
        return data_path.read_bytes()

    def ref(self, handle: str) -> ArtifactRef:
        """Return the :class:`ArtifactRef` metadata for *handle*.

        Served from the in-memory index (#497).

        Raises:
            ArtifactNotFoundError: If *handle* is not in the store.
        """
        _validate_handle(handle)
        ref = self._index.get(handle)
        if ref is None:
            raise ArtifactNotFoundError(f"Artifact not found: {handle!r}")
        return ref

    def list_refs(self) -> list[ArtifactRef]:
        """Return all stored :class:`ArtifactRef` objects, sorted by handle.

        Reads the in-memory index rather than rescanning the directory (#497).
        """
        with self._lock:
            return [self._index[k] for k in sorted(self._index)]

    def delete(self, handle: str) -> None:
        """Remove the artifact identified by *handle*.

        Both the metadata and data files are removed.  If neither file nor an
        index entry exists the operation raises :class:`ArtifactNotFoundError`;
        if only one file is present (e.g. after a crash) both are cleaned up.

        Raises:
            ArtifactNotFoundError: If *handle* is not in the store.
        """
        meta = self._meta_path(handle)
        data = self._data_path(handle)
        with self._lock:
            if handle not in self._index and not meta.is_file() and not data.is_file():
                raise ArtifactNotFoundError(f"Artifact not found: {handle!r}")
            meta.unlink(missing_ok=True)
            data.unlink(missing_ok=True)
            previous = self._index.pop(handle, None)
            if previous is not None:
                self._total_bytes -= previous.size_bytes

    def exists(self, handle: str) -> bool:
        """Return ``True`` if *handle* is in the store."""
        _validate_handle(handle)
        return handle in self._index

    def metadata(self, handle: str) -> ArtifactRef:
        """Return the :class:`ArtifactRef` for *handle*.

        Alias for :meth:`ref` provided for API symmetry with
        :class:`~contextweaver.store.artifacts.InMemoryArtifactStore`.
        """
        return self.ref(handle)

    def drilldown(self, handle: str, selector: dict[str, Any]) -> str:
        """Return a subset of the artifact's content according to *selector*.

        Uses the same selector dialects as
        :meth:`~contextweaver.store.artifacts.InMemoryArtifactStore.drilldown`
        (``head``, ``lines``, ``json_keys``, ``rows``).

        Raises:
            ArtifactNotFoundError: If *handle* is not in the store.
            ContextWeaverError: If the selector type is unknown.
        """
        raw = self.get(handle).decode("utf-8", errors="replace")
        return _apply_selector(raw, selector)

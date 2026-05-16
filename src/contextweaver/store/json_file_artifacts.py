"""Filesystem-backed artifact store for contextweaver.

A persistent :class:`~contextweaver.store.protocols.ArtifactStore`
implementation that mirrors :class:`~contextweaver.store.artifacts.InMemoryArtifactStore`
on disk: each artifact is stored as a ``{handle}.data`` file (raw bytes)
paired with a ``{handle}.json`` file (the :class:`~contextweaver.types.ArtifactRef`
metadata, JSON-encoded).

Useful when artifacts are large (full API responses, images) and the agent
wants directly-inspectable files for debugging, backup, or external
tooling.  Trade-off vs. the SQLite event log: one file per artifact is
heavier than a single database, but the contents are human-grep-friendly.

Limitations:

- **Single process.**  No advisory locking on writes; running two processes
  against the same ``base_dir`` is unsupported.
- **Handle safety.**  Handles are written verbatim into filenames; path
  separators (``/``, ``\\``) and ``..`` traversal segments are rejected on
  write to keep artifacts inside ``base_dir``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from contextweaver.exceptions import ArtifactNotFoundError, ContextWeaverError
from contextweaver.store.artifacts import _apply_selector
from contextweaver.types import ArtifactRef

logger = logging.getLogger("contextweaver.store")

_META_SUFFIX = ".json"
_DATA_SUFFIX = ".data"
_FORBIDDEN_HANDLE_CHARS: frozenset[str] = frozenset({"/", "\\", "\x00"})


def _validate_handle(handle: str) -> None:
    """Reject handles that would escape ``base_dir`` or contain path separators."""
    if not handle:
        raise ContextWeaverError("Artifact handle must be non-empty")
    if handle in {".", ".."}:
        raise ContextWeaverError(f"Invalid artifact handle: {handle!r}")
    if any(ch in handle for ch in _FORBIDDEN_HANDLE_CHARS):
        raise ContextWeaverError(
            f"Invalid artifact handle (contains path separator or null byte): {handle!r}"
        )


class JsonFileArtifactStore:
    """Filesystem implementation of the :class:`ArtifactStore` protocol.

    All artifacts are stored under *base_dir*.  Each artifact occupies two
    sibling files:

    - ``{base_dir}/{handle}.json`` — :class:`~contextweaver.types.ArtifactRef`
      metadata, serialised via :meth:`ArtifactRef.to_dict` + :func:`json.dumps`.
    - ``{base_dir}/{handle}.data`` — raw bytes.

    The directory is created on instantiation if absent.  Existing
    ``{handle}.json`` files in *base_dir* are visible to :meth:`list_refs`
    immediately, so re-instantiating against an existing directory recovers
    the previous metadata index.
    """

    def __init__(self, base_dir: str | Path) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("json_file_artifacts.init: base_dir=%s", self._base_dir)

    @property
    def base_dir(self) -> Path:
        """Filesystem directory backing this store."""
        return self._base_dir

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _meta_path(self, handle: str) -> Path:
        return self._base_dir / f"{handle}{_META_SUFFIX}"

    def _data_path(self, handle: str) -> Path:
        return self._base_dir / f"{handle}{_DATA_SUFFIX}"

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

        Writes are not atomic across the metadata/data pair — a crash between
        the two writes can leave a stale metadata file.  In practice the
        :meth:`list_refs` / :meth:`get` pair tolerates this because
        :meth:`get` errors loudly when the data file is missing.

        Raises:
            ContextWeaverError: If *handle* contains a path separator,
                ``..``, ``.``, or a null byte.
        """
        _validate_handle(handle)
        ref = ArtifactRef(
            handle=handle,
            media_type=media_type,
            size_bytes=len(content),
            label=label,
        )
        self._data_path(handle).write_bytes(content)
        self._meta_path(handle).write_text(
            json.dumps(ref.to_dict(), sort_keys=True), encoding="utf-8"
        )
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

        Raises:
            ArtifactNotFoundError: If *handle* is not in the store.
        """
        meta_path = self._meta_path(handle)
        if not meta_path.is_file():
            raise ArtifactNotFoundError(f"Artifact not found: {handle!r}")
        raw = json.loads(meta_path.read_text(encoding="utf-8"))
        return ArtifactRef.from_dict(raw)

    def list_refs(self) -> list[ArtifactRef]:
        """Return all stored :class:`ArtifactRef` objects, sorted by handle.

        Scans the directory for ``*.json`` files; entries whose JSON does not
        decode into an :class:`ArtifactRef` are skipped silently (logged at
        ``DEBUG``).  Use :meth:`ref` if you need a per-handle error.
        """
        refs: list[ArtifactRef] = []
        for meta in sorted(self._base_dir.glob(f"*{_META_SUFFIX}")):
            try:
                raw = json.loads(meta.read_text(encoding="utf-8"))
                refs.append(ArtifactRef.from_dict(raw))
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                logger.debug("json_file_artifacts.list_refs: skip %s (%s)", meta.name, exc)
        return refs

    def delete(self, handle: str) -> None:
        """Remove the artifact identified by *handle*.

        Both the metadata and data files are removed.  If either is missing
        the operation raises :class:`ArtifactNotFoundError` — both files must
        exist for the artifact to count as present.

        Raises:
            ArtifactNotFoundError: If *handle* is not in the store.
        """
        meta = self._meta_path(handle)
        data = self._data_path(handle)
        if not meta.is_file() and not data.is_file():
            raise ArtifactNotFoundError(f"Artifact not found: {handle!r}")
        meta.unlink(missing_ok=True)
        data.unlink(missing_ok=True)

    def exists(self, handle: str) -> bool:
        """Return ``True`` if *handle* is in the store."""
        return self._data_path(handle).is_file()

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

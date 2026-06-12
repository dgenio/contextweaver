"""In-memory artifact store for contextweaver.

Raw tool outputs are stored out-of-band here.  The LLM context pipeline
receives only :class:`~contextweaver.types.ArtifactRef` handles and summaries.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from typing import Any

from contextweaver.exceptions import ArtifactNotFoundError, ContextWeaverError
from contextweaver.types import ArtifactRef

logger = logging.getLogger("contextweaver.store")


def _apply_selector(raw: str, selector: dict[str, Any]) -> str:
    """Apply a drilldown *selector* to decoded artifact *raw* text.

    Shared by every :class:`~contextweaver.store.protocols.ArtifactStore`
    implementation so the four selector dialects (``head``, ``lines``,
    ``json_keys``, ``rows``) stay byte-for-byte identical across backends.

    Args:
        raw: The artifact's bytes, decoded as UTF-8 (errors replaced).
        selector: A dict whose ``type`` key picks one of the supported
            dialects; remaining keys are dialect-specific.

    Returns:
        The selected subset of *raw* as a string.

    Raises:
        ContextWeaverError: If ``selector["type"]`` is not recognised.
    """
    sel_type = selector.get("type", "")

    if sel_type == "head":
        chars = selector.get("chars", 500)
        return raw[:chars]

    if sel_type == "lines":
        lines = raw.splitlines()
        start = selector.get("start", 0)
        end = selector.get("end", len(lines))
        return "\n".join(lines[start:end])

    if sel_type == "json_keys":
        keys: list[str] = selector.get("keys", [])
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return ""
        if isinstance(obj, dict):
            return json.dumps({k: obj[k] for k in keys if k in obj}, indent=2, sort_keys=True)
        return ""

    if sel_type == "rows":
        # FUTURE: CSV/TSV-aware parsing — detect delimiter, preserve header,
        # and support column filtering.  Currently identical to "lines".
        lines = raw.splitlines()
        start = selector.get("start", 0)
        end = selector.get("end", len(lines))
        return "\n".join(lines[start:end])

    raise ContextWeaverError(f"Unknown drilldown selector type: {sel_type!r}")


class InMemoryArtifactStore:
    """Thread-*unsafe* in-memory implementation of the artifact store.

    Suitable for single-threaded usage and unit tests.  Replace with a
    persistent backend (database, object-storage) for production workloads.
    """

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}
        self._meta: dict[str, ArtifactRef] = {}

    def put(
        self,
        handle: str,
        content: bytes,
        media_type: str = "application/octet-stream",
        label: str = "",
    ) -> ArtifactRef:
        """Store *content* under *handle* and return an :class:`~contextweaver.types.ArtifactRef`.

        Args:
            handle: Unique string key for the artifact.
            content: Raw bytes to store.
            media_type: MIME type of the content.
            label: Human-readable label for display.

        Returns:
            An :class:`~contextweaver.types.ArtifactRef` pointing to the stored artifact.
        """
        ref = ArtifactRef(
            handle=handle,
            media_type=media_type,
            size_bytes=len(content),
            label=label,
            content_hash=hashlib.sha256(content).hexdigest(),
        )
        self._data[handle] = content
        self._meta[handle] = ref
        logger.debug("artifact_store.put: handle=%s, size=%d", handle, len(content))
        return ref

    def get(self, handle: str) -> bytes:
        """Retrieve the raw bytes for *handle*.

        Args:
            handle: The artifact handle returned by :meth:`put`.

        Returns:
            The stored bytes.

        Raises:
            ArtifactNotFoundError: If *handle* is not in the store.
        """
        if handle not in self._data:
            raise ArtifactNotFoundError(f"Artifact not found: {handle!r}")
        return self._data[handle]

    def ref(self, handle: str) -> ArtifactRef:
        """Return the :class:`~contextweaver.types.ArtifactRef` metadata for *handle*.

        Args:
            handle: The artifact handle returned by :meth:`put`.

        Returns:
            The corresponding :class:`~contextweaver.types.ArtifactRef`.

        Raises:
            ArtifactNotFoundError: If *handle* is not in the store.
        """
        if handle not in self._meta:
            raise ArtifactNotFoundError(f"Artifact not found: {handle!r}")
        return self._meta[handle]

    def delete(self, handle: str) -> None:
        """Remove the artifact identified by *handle*.

        Args:
            handle: The artifact handle to delete.

        Raises:
            ArtifactNotFoundError: If *handle* is not in the store.
        """
        if handle not in self._data:
            raise ArtifactNotFoundError(f"Artifact not found: {handle!r}")
        del self._data[handle]
        del self._meta[handle]

    def list_refs(self) -> list[ArtifactRef]:
        """Return all stored :class:`~contextweaver.types.ArtifactRef` objects, sorted by handle.

        Returns:
            A list of :class:`~contextweaver.types.ArtifactRef` sorted by *handle*.
        """
        return [self._meta[k] for k in sorted(self._meta)]

    def exists(self, handle: str) -> bool:
        """Return ``True`` if *handle* is in the store.

        Args:
            handle: The artifact handle to check.
        """
        return handle in self._data

    def metadata(self, handle: str) -> ArtifactRef:
        """Return the :class:`~contextweaver.types.ArtifactRef` metadata for *handle*.

        This is an alias for :meth:`ref` provided for API symmetry.

        Args:
            handle: The artifact handle.

        Raises:
            ArtifactNotFoundError: If *handle* is not in the store.
        """
        return self.ref(handle)

    def drilldown(self, handle: str, selector: dict[str, Any]) -> str:
        """Return a subset of the artifact's content according to *selector*.

        Supported selector types:

        - ``{"type": "head", "chars": N}`` — first *N* characters.
        - ``{"type": "lines", "start": S, "end": E}`` — lines *S* through *E* (exclusive).
        - ``{"type": "json_keys", "keys": [...]}`` — values for given top-level JSON keys.
        - ``{"type": "rows", "start": S, "end": E}`` — rows *S* through *E* of a CSV/TSV-like text.

        Args:
            handle: The artifact handle.
            selector: A dict describing how to slice the content.

        Returns:
            A string representation of the selected portion.

        Raises:
            ArtifactNotFoundError: If *handle* is not in the store.
            ContextWeaverError: If the selector type is unknown.
        """
        raw = self.get(handle).decode("utf-8", errors="replace")
        return _apply_selector(raw, selector)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the store's metadata index *and* raw bytes to a JSON dict.

        Both the :class:`~contextweaver.types.ArtifactRef` index and the raw
        artifact bytes (base64-encoded under ``data``) are emitted, so a
        round-trip through :meth:`from_dict` is lossless: ``get()`` and
        ``drilldown()`` keep working on the restored store (issue #466).  This
        is what lets a :class:`~contextweaver.store.bundle.StoreBundle` carry
        firewalled artifacts across a process restart rather than handing back
        a store whose handles resolve to nothing.
        """
        return {
            "refs": [ref.to_dict() for ref in self.list_refs()],
            "data": {
                handle: base64.b64encode(self._data[handle]).decode("ascii")
                for handle in sorted(self._data)
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InMemoryArtifactStore:
        """Deserialise from a JSON-compatible dict produced by :meth:`to_dict`.

        Both the metadata index and the raw bytes are restored (issue #466), so
        the round-tripped store is fully consistent: a handle present in
        :meth:`list_refs` / :meth:`ref` also resolves through :meth:`get` and
        :meth:`drilldown`.  Refs without a matching ``data`` entry (e.g. a
        legacy payload serialised before #466) restore metadata-only and raise
        :class:`~contextweaver.exceptions.ArtifactNotFoundError` on ``get()``.
        """
        store = cls()
        encoded: dict[str, str] = data.get("data", {})
        for raw in data.get("refs", []):
            ref = ArtifactRef.from_dict(raw)
            store._meta[ref.handle] = ref
            if ref.handle in encoded:
                store._data[ref.handle] = base64.b64decode(encoded[ref.handle])
        return store

"""In-memory artifact store for contextweaver.

Raw tool outputs are stored out-of-band here.  The LLM context pipeline
receives only :class:`~contextweaver.types.ArtifactRef` handles and summaries.
"""

from __future__ import annotations

from typing import Any

from contextweaver.exceptions import ArtifactNotFoundError
from contextweaver.types import ArtifactRef


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
        )
        self._data[handle] = content
        self._meta[handle] = ref
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

    def to_dict(self) -> dict[str, Any]:
        """Serialise the store's metadata index to a JSON-compatible dict."""
        return {"refs": [ref.to_dict() for ref in self.list_refs()]}

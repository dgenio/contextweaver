"""In-memory artifact store for contextweaver.

Raw tool outputs are stored out-of-band here.
"""

from __future__ import annotations

import json
import time
from typing import Any, Protocol, runtime_checkable

from contextweaver.exceptions import ArtifactNotFoundError


@runtime_checkable
class ArtifactStore(Protocol):
    """Read/write interface to the out-of-band artifact store."""

    async def put(
        self,
        handle: str,
        payload: str | bytes,
        metadata: dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> None: ...

    async def get(self, handle: str) -> tuple[str | bytes, dict[str, Any]]: ...
    async def exists(self, handle: str) -> bool: ...
    async def delete(self, handle: str) -> None: ...
    async def metadata(self, handle: str) -> dict[str, Any]: ...
    async def drilldown(self, handle: str, selector: dict[str, Any]) -> str: ...


class InMemoryArtifactStore:
    """Default in-memory ArtifactStore with optional TTL eviction + drilldown."""

    def __init__(self) -> None:
        self._data: dict[str, str | bytes] = {}
        self._meta: dict[str, dict[str, Any]] = {}
        self._ttl: dict[str, float] = {}

    def _evict_expired(self) -> None:
        now = time.time()
        expired = [h for h, exp in self._ttl.items() if exp <= now]
        for h in expired:
            self._data.pop(h, None)
            self._meta.pop(h, None)
            del self._ttl[h]

    async def put(
        self,
        handle: str,
        payload: str | bytes,
        metadata: dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        """Store *payload* under *handle*."""
        self._data[handle] = payload
        self._meta[handle] = metadata or {}
        if ttl_seconds is not None:
            self._ttl[handle] = time.time() + ttl_seconds

    async def get(self, handle: str) -> tuple[str | bytes, dict[str, Any]]:
        """Retrieve payload and metadata. Raises ArtifactNotFoundError."""
        self._evict_expired()
        if handle not in self._data:
            raise ArtifactNotFoundError(f"Artifact not found: {handle!r}")
        return self._data[handle], dict(self._meta[handle])

    async def exists(self, handle: str) -> bool:
        """Check if *handle* exists."""
        self._evict_expired()
        return handle in self._data

    async def delete(self, handle: str) -> None:
        """Remove the artifact. Raises ArtifactNotFoundError."""
        if handle not in self._data:
            raise ArtifactNotFoundError(f"Artifact not found: {handle!r}")
        del self._data[handle]
        del self._meta[handle]
        self._ttl.pop(handle, None)

    async def metadata(self, handle: str) -> dict[str, Any]:
        """Return metadata for *handle*."""
        self._evict_expired()
        if handle not in self._meta:
            raise ArtifactNotFoundError(f"Artifact not found: {handle!r}")
        return dict(self._meta[handle])

    async def drilldown(self, handle: str, selector: dict[str, Any]) -> str:
        """Structured drilldown into artifact contents.

        Selectors:
        - {"type": "lines", "start": 0, "end": 10}
        - {"type": "json_keys", "keys": ["name", "status"]}
        - {"type": "rows", "start": 0, "end": 5}
        - {"type": "head", "chars": 500}
        """
        self._evict_expired()
        if handle not in self._data:
            raise ArtifactNotFoundError(f"Artifact not found: {handle!r}")
        raw = self._data[handle]
        text = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")

        sel_type = selector.get("type", "head")

        if sel_type == "lines":
            lines = text.splitlines()
            start = selector.get("start", 0)
            end = selector.get("end", len(lines))
            return "\n".join(lines[start:end])

        if sel_type == "json_keys":
            keys = selector.get("keys", [])
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    extracted = {k: data[k] for k in keys if k in data}
                    return json.dumps(extracted, indent=2, default=str)
            except (json.JSONDecodeError, TypeError):
                pass
            return text[:500]

        if sel_type == "rows":
            try:
                data = json.loads(text)
                if isinstance(data, list):
                    start = selector.get("start", 0)
                    end = selector.get("end", 5)
                    return json.dumps(data[start:end], indent=2, default=str)
            except (json.JSONDecodeError, TypeError):
                pass
            lines = text.splitlines()
            start = selector.get("start", 0)
            end = selector.get("end", 5)
            return "\n".join(lines[start:end])

        # Default: head
        chars = selector.get("chars", 500)
        return text[:chars]

    def list_refs(self) -> list[str]:
        """Return all stored handles, sorted."""
        self._evict_expired()
        return sorted(self._data.keys())

    def to_dict(self) -> dict[str, Any]:
        """Serialise metadata index."""
        return {"handles": {h: dict(self._meta.get(h, {})) for h in sorted(self._data.keys())}}

"""In-memory fact store for contextweaver.

Durable semantic facts. Key-value, last-write-wins.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class FactStore(Protocol):
    """Durable semantic facts. Key-value, last-write-wins."""

    async def put(self, key: str, value: str, metadata: dict[str, Any] | None = None) -> None: ...

    async def get(self, key: str) -> tuple[str, dict[str, Any]] | None: ...
    async def list_keys(self, prefix: str | None = None) -> list[str]: ...
    async def get_all(self) -> dict[str, str]: ...
    async def delete(self, key: str) -> None: ...


class InMemoryFactStore:
    """Default in-memory FactStore. Dict-backed."""

    def __init__(self) -> None:
        self._facts: dict[str, tuple[str, dict[str, Any]]] = {}

    async def put(self, key: str, value: str, metadata: dict[str, Any] | None = None) -> None:
        """Insert or replace a fact."""
        self._facts[key] = (value, metadata or {})

    async def get(self, key: str) -> tuple[str, dict[str, Any]] | None:
        """Return (value, metadata) or None."""
        if key not in self._facts:
            return None
        value, meta = self._facts[key]
        return value, dict(meta)

    async def list_keys(self, prefix: str | None = None) -> list[str]:
        """Return all keys, optionally filtered by prefix."""
        keys = sorted(self._facts.keys())
        if prefix is not None:
            keys = [k for k in keys if k.startswith(prefix)]
        return keys

    async def get_all(self) -> dict[str, str]:
        """Return all facts as {key: value}."""
        return {k: v for k, (v, _) in sorted(self._facts.items())}

    async def delete(self, key: str) -> None:
        """Remove a fact."""
        if key not in self._facts:
            raise KeyError(f"Fact not found: {key!r}")
        del self._facts[key]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "facts": {
                k: {"value": v, "metadata": dict(m)} for k, (v, m) in sorted(self._facts.items())
            }
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InMemoryFactStore:
        """Deserialise from dict."""
        store = cls()
        for key, entry in data.get("facts", {}).items():
            store._facts[key] = (entry["value"], dict(entry.get("metadata", {})))
        return store

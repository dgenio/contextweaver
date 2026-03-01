"""In-memory episodic memory store for contextweaver.

Rolling summaries of conversation segments or task episodes.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EpisodicStore(Protocol):
    """Rolling summaries of conversation segments or task episodes."""

    async def put(
        self, episode_id: str, summary: str, metadata: dict[str, Any] | None = None
    ) -> None: ...

    async def get(self, episode_id: str) -> tuple[str, dict[str, Any]]: ...
    async def list_episodes(self, limit: int | None = None) -> list[str]: ...
    async def latest(self, n: int = 3) -> list[tuple[str, str, dict[str, Any]]]: ...
    async def delete(self, episode_id: str) -> None: ...


class InMemoryEpisodicStore:
    """Default in-memory EpisodicStore. Ordered by insertion time."""

    def __init__(self) -> None:
        self._episodes: dict[str, tuple[str, dict[str, Any]]] = {}
        self._order: list[str] = []

    async def put(
        self, episode_id: str, summary: str, metadata: dict[str, Any] | None = None
    ) -> None:
        """Store or update a rolling episodic summary."""
        if episode_id not in self._episodes:
            self._order.append(episode_id)
        self._episodes[episode_id] = (summary, metadata or {})

    async def get(self, episode_id: str) -> tuple[str, dict[str, Any]]:
        """Return (summary, metadata) for *episode_id*."""
        if episode_id not in self._episodes:
            raise KeyError(f"Episode not found: {episode_id!r}")
        summary, meta = self._episodes[episode_id]
        return summary, dict(meta)

    async def list_episodes(self, limit: int | None = None) -> list[str]:
        """Return episode IDs in insertion order."""
        if limit is not None:
            return list(self._order[:limit])
        return list(self._order)

    async def latest(self, n: int = 3) -> list[tuple[str, str, dict[str, Any]]]:
        """Return n most recent (episode_id, summary, metadata)."""
        result: list[tuple[str, str, dict[str, Any]]] = []
        for eid in reversed(self._order):
            if len(result) >= n:
                break
            summary, meta = self._episodes[eid]
            result.append((eid, summary, dict(meta)))
        return result

    async def delete(self, episode_id: str) -> None:
        """Remove an episode."""
        if episode_id not in self._episodes:
            raise KeyError(f"Episode not found: {episode_id!r}")
        del self._episodes[episode_id]
        self._order.remove(episode_id)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "episodes": [
                {"episode_id": eid, "summary": s, "metadata": dict(m)}
                for eid in self._order
                for s, m in [self._episodes[eid]]
            ]
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InMemoryEpisodicStore:
        """Deserialise from dict. Uses sync internals for bootstrapping."""
        store = cls()
        for raw in data.get("episodes", []):
            eid = raw["episode_id"]
            store._order.append(eid)
            store._episodes[eid] = (raw["summary"], dict(raw.get("metadata", {})))
        return store

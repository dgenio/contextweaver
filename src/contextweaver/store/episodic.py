"""In-memory episodic memory store for contextweaver.

The episodic store holds compressed summaries of past agent episodes
(conversations / task runs).  Items can be retrieved by recency or by
similarity to a query for injection into future contexts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from contextweaver._utils import TfIdfScorer, jaccard, tokenize
from contextweaver.exceptions import ItemNotFoundError

# FUTURE: vector retrieval backend for high-dimensional similarity search.


@dataclass
class Episode:
    """A single episodic memory entry."""

    episode_id: str
    summary: str
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "episode_id": self.episode_id,
            "summary": self.summary,
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Episode:
        """Deserialise from a JSON-compatible dict."""
        return cls(
            episode_id=data["episode_id"],
            summary=data["summary"],
            tags=list(data.get("tags", [])),
            metadata=dict(data.get("metadata", {})),
        )


class InMemoryEpisodicStore:
    """In-memory episodic store with similarity-based retrieval.

    Episodes are stored in insertion order.  Similarity search uses
    :class:`~contextweaver._utils.TfIdfScorer` backed by :func:`~contextweaver._utils.jaccard`
    as a fallback for very small corpora.
    """

    def __init__(self) -> None:
        self._episodes: list[Episode] = []
        self._scorer: TfIdfScorer = TfIdfScorer()
        self._dirty: bool = False

    def add(self, episode: Episode) -> None:
        """Append *episode* to the store.

        Args:
            episode: The episode to store.
        """
        self._episodes.append(episode)
        self._dirty = True

    def get(self, episode_id: str) -> Episode | None:
        """Return the episode with *episode_id*, or ``None`` if not found.

        Args:
            episode_id: The unique identifier to look up.
        """
        for ep in self._episodes:
            if ep.episode_id == episode_id:
                return ep
        return None

    def _ensure_index(self) -> None:
        if self._dirty:
            self._scorer.fit([ep.summary for ep in self._episodes])
            self._dirty = False

    def search(self, query: str, top_k: int = 5) -> list[Episode]:
        """Return the *top_k* most relevant episodes for *query*.

        Uses TF-IDF scoring; falls back to Jaccard when the corpus is empty.

        Args:
            query: Raw query string.
            top_k: Maximum number of results to return.

        Returns:
            A list of up to *top_k* episodes, most relevant first.
        """
        if not self._episodes:
            return []
        self._ensure_index()
        scores = self._scorer.score_all(query)
        # Jaccard fallback when all TF-IDF scores are 0
        if all(s == 0.0 for s in scores):
            q_tokens = tokenize(query)
            scores = [jaccard(q_tokens, tokenize(ep.summary)) for ep in self._episodes]
        ranked = sorted(range(len(self._episodes)), key=lambda i: scores[i], reverse=True)
        return [self._episodes[i] for i in ranked[:top_k]]

    def all(self) -> list[Episode]:
        """Return all episodes in insertion order."""
        return list(self._episodes)

    def latest(self, n: int = 3) -> list[tuple[str, str, dict[str, Any]]]:
        """Return the *n* most recently added episodes.

        Args:
            n: Number of most-recent episodes to return.

        Returns:
            A list of ``(episode_id, summary, metadata)`` tuples, most-recent first.
        """
        recent = self._episodes[-n:] if n > 0 else []
        return [(ep.episode_id, ep.summary, dict(ep.metadata)) for ep in reversed(recent)]

    def delete(self, episode_id: str) -> None:
        """Remove the episode with *episode_id*.

        Args:
            episode_id: The unique identifier of the episode to remove.

        Raises:
            ItemNotFoundError: If no episode with *episode_id* exists.
        """
        for i, ep in enumerate(self._episodes):
            if ep.episode_id == episode_id:
                self._episodes.pop(i)
                self._dirty = True
                return
        raise ItemNotFoundError(f"Episode not found: {episode_id!r}")

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {"episodes": [ep.to_dict() for ep in self._episodes]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InMemoryEpisodicStore:
        """Deserialise from a JSON-compatible dict produced by :meth:`to_dict`."""
        store = cls()
        for raw in data.get("episodes", []):
            store.add(Episode.from_dict(raw))
        return store

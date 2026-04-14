"""Store sub-package for contextweaver.

Exports the four in-memory store implementations and the :class:`StoreBundle`
convenience wrapper.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.store.episodic import InMemoryEpisodicStore
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.store.facts import InMemoryFactStore


@dataclass
class StoreBundle:
    """Groups the four optional stores used by the Context Engine.

    All fields default to ``None``; the engine creates in-memory defaults for
    any store left as ``None`` at build time.
    """

    artifact_store: InMemoryArtifactStore | None = field(default=None)
    event_log: InMemoryEventLog | None = field(default=None)
    episodic_store: InMemoryEpisodicStore | None = field(default=None)
    fact_store: InMemoryFactStore | None = field(default=None)

    def to_dict(self) -> dict[str, Any]:
        """Serialise non-None stores to a JSON-compatible dict."""
        return {
            "artifact_store": self.artifact_store.to_dict() if self.artifact_store else None,
            "event_log": self.event_log.to_dict() if self.event_log else None,
            "episodic_store": self.episodic_store.to_dict() if self.episodic_store else None,
            "fact_store": self.fact_store.to_dict() if self.fact_store else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StoreBundle:
        """Deserialise from a JSON-compatible dict produced by :meth:`to_dict`."""
        raw_artifact = data.get("artifact_store")
        raw_event_log = data.get("event_log")
        raw_episodic = data.get("episodic_store")
        raw_fact = data.get("fact_store")
        return cls(
            artifact_store=InMemoryArtifactStore.from_dict(raw_artifact)
            if raw_artifact is not None
            else None,
            event_log=InMemoryEventLog.from_dict(raw_event_log)
            if raw_event_log is not None
            else None,
            episodic_store=InMemoryEpisodicStore.from_dict(raw_episodic)
            if raw_episodic is not None
            else None,
            fact_store=InMemoryFactStore.from_dict(raw_fact)
            if raw_fact is not None
            else None,
        )


__all__ = [
    "InMemoryArtifactStore",
    "InMemoryEpisodicStore",
    "InMemoryEventLog",
    "InMemoryFactStore",
    "StoreBundle",
]

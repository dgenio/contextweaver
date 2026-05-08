"""StoreBundle: groups the four optional stores used by the Context Engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.store.episodic import InMemoryEpisodicStore
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.store.facts import InMemoryFactStore

if TYPE_CHECKING:
    from contextweaver.protocols import ArtifactStore, EpisodicStore, EventLog, FactStore


@dataclass
class StoreBundle:
    """Groups the four optional stores used by the Context Engine.

    All fields default to ``None``; the engine creates in-memory defaults for
    any store left as ``None`` at build time.
    """

    artifact_store: ArtifactStore | None = field(default=None)
    event_log: EventLog | None = field(default=None)
    episodic_store: EpisodicStore | None = field(default=None)
    fact_store: FactStore | None = field(default=None)

    def to_dict(self) -> dict[str, Any]:
        """Serialise non-None stores to a JSON-compatible dict.

        Only works with store implementations that provide a ``to_dict()``
        method (e.g. the built-in InMemory stores).
        """

        def _ser(store: object) -> object:
            if store is not None and hasattr(store, "to_dict"):
                return store.to_dict()  # pyright: ignore[reportAttributeAccessIssue]
            return None

        return {
            "artifact_store": _ser(self.artifact_store),
            "event_log": _ser(self.event_log),
            "episodic_store": _ser(self.episodic_store),
            "fact_store": _ser(self.fact_store),
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
            fact_store=InMemoryFactStore.from_dict(raw_fact) if raw_fact is not None else None,
        )

"""Store sub-package for contextweaver.

Exports the four in-memory store implementations and the StoreBundle wrapper.
"""

from __future__ import annotations

from dataclasses import dataclass

from contextweaver.store.artifacts import ArtifactStore, InMemoryArtifactStore
from contextweaver.store.episodic import EpisodicStore, InMemoryEpisodicStore
from contextweaver.store.event_log import EventLog, InMemoryEventLog
from contextweaver.store.facts import FactStore, InMemoryFactStore


@dataclass
class StoreBundle:
    """Groups all four stores for clean ContextManager initialization.

    All fields default to None; ContextManager creates InMemory* instances
    for any that are None.
    """

    artifact_store: ArtifactStore | None = None
    event_log: EventLog | None = None
    episodic_store: EpisodicStore | None = None
    fact_store: FactStore | None = None


__all__ = [
    "ArtifactStore",
    "EpisodicStore",
    "EventLog",
    "FactStore",
    "InMemoryArtifactStore",
    "InMemoryEpisodicStore",
    "InMemoryEventLog",
    "InMemoryFactStore",
    "StoreBundle",
]

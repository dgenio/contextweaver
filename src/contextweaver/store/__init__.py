"""Store sub-package for contextweaver.

Exports the in-memory store implementations, the persistent
:class:`SqliteEventLog` / :class:`JsonFileArtifactStore` backends, the
:class:`StoreBundle` convenience wrapper, and the async-protocol bridges
(:func:`to_async` / :func:`to_sync`, issue #495) for using a store under the
opposite (async/sync) interface.
"""

from __future__ import annotations

from contextweaver.store._async_to_sync import is_async_store, to_sync
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.store.async_bridge import to_async
from contextweaver.store.bundle import StoreBundle
from contextweaver.store.episodic import InMemoryEpisodicStore
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.store.facts import InMemoryFactStore
from contextweaver.store.json_file_artifacts import JsonFileArtifactStore
from contextweaver.store.sqlite_episodic import SqliteEpisodicStore
from contextweaver.store.sqlite_event_log import SqliteEventLog
from contextweaver.store.sqlite_facts import SqliteFactStore

__all__ = [
    "InMemoryArtifactStore",
    "InMemoryEpisodicStore",
    "InMemoryEventLog",
    "InMemoryFactStore",
    "JsonFileArtifactStore",
    "SqliteEpisodicStore",
    "SqliteEventLog",
    "SqliteFactStore",
    "StoreBundle",
    "is_async_store",
    "to_async",
    "to_sync",
]

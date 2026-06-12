"""Sync-to-async store bridges (issue #495).

Wrap any *synchronous* store as the matching async protocol from
:mod:`contextweaver.store.async_protocols` by offloading each call to a worker
thread with :func:`asyncio.to_thread`.  Because ``to_thread`` yields control
back to the event loop while the (blocking) sync method runs, an ``await`` on a
bridged store never blocks the loop — so every existing backend works under the
async interface immediately, including the slow/network-bound ones.

The public entry point is :func:`to_async`, which detects the store's role from
its method surface and returns the corresponding async bridge.  The inverse
direction (async store -> sync protocol, used by
:class:`~contextweaver.context.manager.ContextManager`) lives in
:mod:`contextweaver.store._async_to_sync`.

Caveat — thread affinity: :func:`asyncio.to_thread` dispatches to a worker pool,
so the wrapped store must be safe to call from a thread other than the one that
created it.  The in-memory and JSON-file backends qualify (the latter guards
its state with a lock).  A *thread-affine* backend such as
:class:`~contextweaver.store.sqlite_event_log.SqliteEventLog` (its connection is
opened with ``check_same_thread=True``) is **not** a valid target for
:func:`to_async`; its async story is a future native ``aiosqlite`` backend.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from contextweaver.store.async_protocols import (
    AsyncArtifactStore,
    AsyncEpisodicStore,
    AsyncEventLog,
    AsyncFactStore,
)

if TYPE_CHECKING:
    from contextweaver.store.episodic import Episode
    from contextweaver.store.facts import Fact
    from contextweaver.store.protocols import ArtifactStore, EpisodicStore, EventLog, FactStore
    from contextweaver.types import ArtifactRef, ContextItem, ItemKind


class _AsyncEventLogBridge:
    """Expose a sync :class:`EventLog` through the :class:`AsyncEventLog` protocol."""

    def __init__(self, inner: EventLog) -> None:
        self._inner = inner

    async def append(self, item: ContextItem) -> None:
        await asyncio.to_thread(self._inner.append, item)

    async def get(self, item_id: str) -> ContextItem:
        return await asyncio.to_thread(self._inner.get, item_id)

    async def all(self) -> list[ContextItem]:
        return await asyncio.to_thread(self._inner.all)

    async def filter_by_kind(self, *kinds: ItemKind) -> list[ContextItem]:
        return await asyncio.to_thread(self._inner.filter_by_kind, *kinds)

    async def tail(self, n: int) -> list[ContextItem]:
        return await asyncio.to_thread(self._inner.tail, n)

    async def children(self, parent_id: str) -> list[ContextItem]:
        return await asyncio.to_thread(self._inner.children, parent_id)

    async def parent(self, item_id: str) -> ContextItem | None:
        return await asyncio.to_thread(self._inner.parent, item_id)

    async def query(
        self,
        kinds: list[ItemKind] | None = None,
        since: int | None = None,
        limit: int | None = None,
    ) -> list[ContextItem]:
        return await asyncio.to_thread(self._inner.query, kinds, since, limit)

    async def count(self) -> int:
        return await asyncio.to_thread(self._inner.count)

    async def close(self) -> None:
        await asyncio.to_thread(self._inner.close)


class _AsyncArtifactStoreBridge:
    """Expose a sync :class:`ArtifactStore` through the :class:`AsyncArtifactStore` protocol."""

    def __init__(self, inner: ArtifactStore) -> None:
        self._inner = inner

    async def put(
        self,
        handle: str,
        content: bytes,
        media_type: str = "application/octet-stream",
        label: str = "",
    ) -> ArtifactRef:
        return await asyncio.to_thread(self._inner.put, handle, content, media_type, label)

    async def get(self, handle: str) -> bytes:
        return await asyncio.to_thread(self._inner.get, handle)

    async def ref(self, handle: str) -> ArtifactRef:
        return await asyncio.to_thread(self._inner.ref, handle)

    async def list_refs(self) -> list[ArtifactRef]:
        return await asyncio.to_thread(self._inner.list_refs)

    async def delete(self, handle: str) -> None:
        await asyncio.to_thread(self._inner.delete, handle)

    async def exists(self, handle: str) -> bool:
        return await asyncio.to_thread(self._inner.exists, handle)

    async def metadata(self, handle: str) -> ArtifactRef:
        return await asyncio.to_thread(self._inner.metadata, handle)

    async def drilldown(self, handle: str, selector: dict[str, Any]) -> str:
        return await asyncio.to_thread(self._inner.drilldown, handle, selector)


class _AsyncEpisodicStoreBridge:
    """Expose a sync :class:`EpisodicStore` through the :class:`AsyncEpisodicStore` protocol."""

    def __init__(self, inner: EpisodicStore) -> None:
        self._inner = inner

    async def add(self, episode: Episode) -> None:
        await asyncio.to_thread(self._inner.add, episode)

    async def get(self, episode_id: str) -> Episode | None:
        return await asyncio.to_thread(self._inner.get, episode_id)

    async def search(self, query: str, top_k: int = 5) -> list[Episode]:
        return await asyncio.to_thread(self._inner.search, query, top_k)

    async def all(self) -> list[Episode]:
        return await asyncio.to_thread(self._inner.all)

    async def latest(self, n: int = 3) -> list[tuple[str, str, dict[str, Any]]]:
        return await asyncio.to_thread(self._inner.latest, n)

    async def delete(self, episode_id: str) -> None:
        await asyncio.to_thread(self._inner.delete, episode_id)


class _AsyncFactStoreBridge:
    """Expose a sync :class:`FactStore` through the :class:`AsyncFactStore` protocol."""

    def __init__(self, inner: FactStore) -> None:
        self._inner = inner

    async def put(self, fact: Fact) -> None:
        await asyncio.to_thread(self._inner.put, fact)

    async def get(self, fact_id: str) -> Fact:
        return await asyncio.to_thread(self._inner.get, fact_id)

    async def get_by_key(self, key: str) -> list[Fact]:
        return await asyncio.to_thread(self._inner.get_by_key, key)

    async def list_keys(self, prefix: str = "") -> list[str]:
        return await asyncio.to_thread(self._inner.list_keys, prefix)

    async def delete(self, fact_id: str) -> None:
        await asyncio.to_thread(self._inner.delete, fact_id)

    async def all(self) -> list[Fact]:
        return await asyncio.to_thread(self._inner.all)


def to_async(
    store: EventLog | ArtifactStore | EpisodicStore | FactStore,
) -> AsyncEventLog | AsyncArtifactStore | AsyncEpisodicStore | AsyncFactStore:
    """Wrap a synchronous *store* as its matching async protocol.

    The store's role is detected from its method surface (``append``/``tail`` →
    event log, ``ref``/``drilldown`` → artifacts, ``latest``/``add`` →
    episodic, ``get_by_key``/``list_keys`` → facts).  An already-async store is
    returned unchanged.

    Raises:
        TypeError: If *store* matches none of the four store roles.
    """
    # Detect "already async" by coroutine methods rather than a structural
    # protocol match (the sync/async protocols share method *names*).
    if _is_async(store):
        return store  # type: ignore[return-value]
    if hasattr(store, "append") and hasattr(store, "tail"):
        return _AsyncEventLogBridge(store)  # type: ignore[arg-type]
    if hasattr(store, "drilldown") and hasattr(store, "ref"):
        return _AsyncArtifactStoreBridge(store)  # type: ignore[arg-type]
    if hasattr(store, "latest") and hasattr(store, "add"):
        return _AsyncEpisodicStoreBridge(store)  # type: ignore[arg-type]
    if hasattr(store, "get_by_key") and hasattr(store, "list_keys"):
        return _AsyncFactStoreBridge(store)
    raise TypeError(f"object {store!r} does not match any store protocol")


def _is_async(store: object) -> bool:
    """Return ``True`` if *store*'s representative method is a coroutine function."""
    import inspect

    for name in ("append", "put", "add"):
        method = getattr(store, name, None)
        if method is not None:
            return inspect.iscoroutinefunction(method)
    return False

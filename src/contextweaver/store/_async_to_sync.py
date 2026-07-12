"""Async-to-sync store bridges (issue #495).

The inverse of :mod:`contextweaver.store.async_bridge`: wrap an *async* store
(one implementing an :mod:`contextweaver.store.async_protocols` protocol) so it
satisfies the corresponding **synchronous** protocol from
:mod:`contextweaver.store.protocols`.  This lets the existing synchronous
context pipeline — and :class:`~contextweaver.context.manager.ContextManager` —
consume an async backend without being rewritten.

Each bridge drives the wrapped coroutine on a private event loop running in a
dedicated daemon thread (:class:`_LoopThread`).  The caller's thread blocks on
the result, but the caller's *event loop* (if any) is never blocked, because
the awaited I/O runs on the private loop.  During an async
:meth:`ContextManager.build`, the synchronous pipeline body is offloaded to a
worker thread (so it is that worker, not the main loop, that blocks here),
keeping the main loop responsive — see the manager's ``_async_backed`` path.

This module is internal (underscore-prefixed); construct bridges via
:func:`to_sync` and detect store flavour via :func:`is_async_store`.
"""

from __future__ import annotations

import inspect
from types import TracebackType
from typing import TYPE_CHECKING, Any

# ``_LoopThread`` was extracted to ``_loop_thread`` to keep this module within
# the 300-line ceiling; imported here for the bridge/``to_sync`` type hints.
from contextweaver.store._loop_thread import _LoopThread

if TYPE_CHECKING:
    from contextweaver.store.async_protocols import (
        AsyncArtifactStore,
        AsyncEpisodicStore,
        AsyncEventLog,
        AsyncFactStore,
    )
    from contextweaver.store.episodic import Episode
    from contextweaver.store.facts import Fact
    from contextweaver.store.protocols import ArtifactStore, EpisodicStore, EventLog, FactStore
    from contextweaver.types import ArtifactRef, ContextItem, ItemKind


class _SyncEventLogBridge:
    """Expose an :class:`AsyncEventLog` through the sync :class:`EventLog` protocol."""

    def __init__(self, inner: AsyncEventLog, loop: _LoopThread) -> None:
        self._inner = inner
        self._loop = loop

    def append(self, item: ContextItem) -> None:
        self._loop.run(self._inner.append(item))

    def get(self, item_id: str) -> ContextItem:
        return self._loop.run(self._inner.get(item_id))

    def all(self) -> list[ContextItem]:
        return self._loop.run(self._inner.all())

    def filter_by_kind(self, *kinds: ItemKind) -> list[ContextItem]:
        return self._loop.run(self._inner.filter_by_kind(*kinds))

    def tail(self, n: int) -> list[ContextItem]:
        return self._loop.run(self._inner.tail(n))

    def children(self, parent_id: str) -> list[ContextItem]:
        return self._loop.run(self._inner.children(parent_id))

    def parent(self, item_id: str) -> ContextItem | None:
        return self._loop.run(self._inner.parent(item_id))

    def query(
        self,
        kinds: list[ItemKind] | None = None,
        since: int | None = None,
        limit: int | None = None,
    ) -> list[ContextItem]:
        return self._loop.run(self._inner.query(kinds, since, limit))

    def count(self) -> int:
        return self._loop.run(self._inner.count())

    def __len__(self) -> int:
        return self.count()

    def close(self) -> None:
        self._loop.run(self._inner.close())

    def __enter__(self) -> _SyncEventLogBridge:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class _SyncArtifactStoreBridge:
    """Expose an :class:`AsyncArtifactStore` through the sync :class:`ArtifactStore` protocol."""

    def __init__(self, inner: AsyncArtifactStore, loop: _LoopThread) -> None:
        self._inner = inner
        self._loop = loop

    def put(
        self,
        handle: str,
        content: bytes,
        media_type: str = "application/octet-stream",
        label: str = "",
    ) -> ArtifactRef:
        return self._loop.run(self._inner.put(handle, content, media_type, label))

    def get(self, handle: str) -> bytes:
        return self._loop.run(self._inner.get(handle))

    def ref(self, handle: str) -> ArtifactRef:
        return self._loop.run(self._inner.ref(handle))

    def list_refs(self) -> list[ArtifactRef]:
        return self._loop.run(self._inner.list_refs())

    def delete(self, handle: str) -> None:
        self._loop.run(self._inner.delete(handle))

    def exists(self, handle: str) -> bool:
        return self._loop.run(self._inner.exists(handle))

    def metadata(self, handle: str) -> ArtifactRef:
        return self._loop.run(self._inner.metadata(handle))

    def drilldown(self, handle: str, selector: dict[str, Any]) -> str:
        return self._loop.run(self._inner.drilldown(handle, selector))


class _SyncEpisodicStoreBridge:
    """Expose an :class:`AsyncEpisodicStore` through the sync :class:`EpisodicStore` protocol."""

    def __init__(self, inner: AsyncEpisodicStore, loop: _LoopThread) -> None:
        self._inner = inner
        self._loop = loop

    def add(self, episode: Episode) -> None:
        self._loop.run(self._inner.add(episode))

    def get(self, episode_id: str) -> Episode | None:
        return self._loop.run(self._inner.get(episode_id))

    def search(self, query: str, top_k: int = 5) -> list[Episode]:
        return self._loop.run(self._inner.search(query, top_k))

    def all(self) -> list[Episode]:
        return self._loop.run(self._inner.all())

    def latest(self, n: int = 3) -> list[tuple[str, str, dict[str, Any]]]:
        return self._loop.run(self._inner.latest(n))

    def delete(self, episode_id: str) -> None:
        self._loop.run(self._inner.delete(episode_id))


class _SyncFactStoreBridge:
    """Expose an :class:`AsyncFactStore` through the sync :class:`FactStore` protocol."""

    def __init__(self, inner: AsyncFactStore, loop: _LoopThread) -> None:
        self._inner = inner
        self._loop = loop

    def put(self, fact: Fact) -> None:
        self._loop.run(self._inner.put(fact))

    def get(self, fact_id: str) -> Fact:
        return self._loop.run(self._inner.get(fact_id))

    def get_by_key(self, key: str) -> list[Fact]:
        return self._loop.run(self._inner.get_by_key(key))

    def list_keys(self, prefix: str = "") -> list[str]:
        return self._loop.run(self._inner.list_keys(prefix))

    def delete(self, fact_id: str) -> None:
        self._loop.run(self._inner.delete(fact_id))

    def all(self) -> list[Fact]:
        return self._loop.run(self._inner.all())


def is_async_store(store: object) -> bool:
    """Return ``True`` if *store* implements an async store protocol.

    Detected by whether the store's representative write method (``append`` /
    ``put`` / ``add``) is a coroutine function — the sync and async protocols
    share method *names*, so a structural ``isinstance`` check cannot tell them
    apart.
    """
    for name in ("append", "put", "add"):
        method = getattr(store, name, None)
        if method is not None:
            return inspect.iscoroutinefunction(method)
    return False


def to_sync(
    store: AsyncEventLog | AsyncArtifactStore | AsyncEpisodicStore | AsyncFactStore,
    loop: _LoopThread,
) -> EventLog | ArtifactStore | EpisodicStore | FactStore:
    """Wrap an async *store* as its matching sync protocol, driven by *loop*.

    Role is detected from the method surface, mirroring
    :func:`contextweaver.store.async_bridge.to_async`.

    Raises:
        TypeError: If *store* matches none of the four store roles.
    """
    if hasattr(store, "append") and hasattr(store, "tail"):
        return _SyncEventLogBridge(store, loop)  # type: ignore[arg-type]
    if hasattr(store, "drilldown") and hasattr(store, "ref"):
        return _SyncArtifactStoreBridge(store, loop)  # type: ignore[arg-type]
    if hasattr(store, "latest") and hasattr(store, "add"):
        return _SyncEpisodicStoreBridge(store, loop)  # type: ignore[arg-type]
    if hasattr(store, "get_by_key") and hasattr(store, "list_keys"):
        return _SyncFactStoreBridge(store, loop)
    raise TypeError(f"object {store!r} does not match any async store protocol")

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

import asyncio
import concurrent.futures
import contextlib
import inspect
import threading
from collections.abc import Coroutine
from types import TracebackType
from typing import TYPE_CHECKING, Any, TypeVar

from contextweaver.exceptions import StoreTimeoutError

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

_T = TypeVar("_T")

#: Default per-operation timeout (seconds) for the sync store bridge.  Generous
#: enough for a slow-but-healthy network backend call (redis/S3/Zep), while
#: still bounding a hung backend so it cannot wedge the loop thread forever
#: (issue #750).  Override per-manager via ``_LoopThread(timeout=...)`` or
#: per-call via ``run(coro, timeout=...)``; ``timeout=None`` waits indefinitely.
_DEFAULT_STORE_OP_TIMEOUT = 30.0

#: Sentinel distinguishing "caller did not pass timeout" (use the instance
#: default) from an explicit ``timeout=None`` (wait forever).
_USE_DEFAULT_TIMEOUT: Any = object()


class _LoopThread:
    """A private asyncio event loop running in its own daemon thread.

    Shared by all async-to-sync bridges attached to one
    :class:`~contextweaver.context.manager.ContextManager`, so async store I/O
    runs off the caller's loop.  :meth:`run` submits a coroutine and blocks the
    calling thread until it completes.

    Args:
        timeout: Default per-operation timeout in seconds applied by
            :meth:`run` when the caller does not override it.  ``None`` waits
            indefinitely (the pre-#750 behaviour).
    """

    def __init__(self, timeout: float | None = _DEFAULT_STORE_OP_TIMEOUT) -> None:
        self._loop = asyncio.new_event_loop()
        self._timeout = timeout
        self._thread = threading.Thread(target=self._serve, name="cw-store-loop", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run(
        self, coro: Coroutine[Any, Any, _T], timeout: float | None = _USE_DEFAULT_TIMEOUT
    ) -> _T:
        """Run *coro* on the private loop and return its result (blocking).

        Blocks the calling thread until the coroutine completes or *timeout*
        seconds elapse.  On timeout the pending coroutine is cancelled and
        :class:`~contextweaver.exceptions.StoreTimeoutError` is raised rather
        than hanging indefinitely (issue #750) — a single stuck backend call
        would otherwise wedge this loop thread and, via the manager build lock,
        every subsequent ``build()``.

        Args:
            coro: The coroutine to drive on the private loop.
            timeout: Seconds to wait; defaults to the instance timeout.
                ``None`` waits forever.
        """
        effective = self._timeout if timeout is _USE_DEFAULT_TIMEOUT else timeout
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=effective)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise StoreTimeoutError(
                f"async store operation did not complete within {effective}s"
            ) from None

    def close(self) -> None:
        """Stop the private loop and join its thread.  Idempotent.

        Cancels and drains any still-pending tasks first — e.g. a coroutine
        abandoned by a :meth:`run` that timed out (issue #750) — so the loop
        stops cleanly without a ``Task was destroyed but it is pending``
        warning to stderr.
        """
        if self._loop.is_closed():
            return

        async def _drain() -> None:
            pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for task in pending:
                task.cancel()
            for task in pending:
                with contextlib.suppress(BaseException):
                    await task

        with contextlib.suppress(concurrent.futures.TimeoutError, RuntimeError):
            asyncio.run_coroutine_threadsafe(_drain(), self._loop).result(timeout=5)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        self._loop.close()


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

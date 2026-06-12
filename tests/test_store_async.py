"""Async store protocols + sync/async bridges (issue #495).

Covers:
- the async conformance kit against every bundled backend wrapped with
  :func:`to_async`;
- round-tripping a sync backend through ``to_sync(to_async(...))``;
- :func:`is_async_store` flavour detection;
- :class:`ContextManager` accepting an async store backend and producing the
  same build output as the sync backend;
- the non-blocking guarantee: an async ``build`` over a deliberately slow async
  store does not block the caller's event loop.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from contextweaver.context.manager import ContextManager
from contextweaver.store import is_async_store, to_async, to_sync
from contextweaver.store._async_to_sync import _LoopThread
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.store.async_protocols import AsyncArtifactStore
from contextweaver.store.bundle import StoreBundle
from contextweaver.store.episodic import Episode, InMemoryEpisodicStore
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.store.facts import Fact, InMemoryFactStore
from contextweaver.store.json_file_artifacts import JsonFileArtifactStore
from contextweaver.store.protocols import EventLog
from contextweaver.store.testing import (
    check_async_artifact_store_conformance,
    check_async_episodic_store_conformance,
    check_async_event_log_conformance,
    check_async_fact_store_conformance,
    check_event_log_conformance,
)
from contextweaver.types import ContextItem, ItemKind

# ---------------------------------------------------------------------------
# Async conformance over to_async(<sync backend>)
# ---------------------------------------------------------------------------


async def test_async_event_log_conformance_in_memory() -> None:
    await check_async_event_log_conformance(lambda: to_async(InMemoryEventLog()))


# NOTE: ``to_async(SqliteEventLog(...))`` is intentionally *not* covered. The
# SQLite connection is opened with ``check_same_thread=True`` (it is
# thread-affine), so driving it from ``asyncio.to_thread``'s worker pool raises
# ``ProgrammingError``. SQLite's async story is a future native ``aiosqlite``
# backend, not a thread bridge — see ``store/async_bridge.py``. Thread-safe
# backends (in-memory, JSON-file) bridge cleanly and are covered here.


async def test_async_artifact_store_conformance_in_memory() -> None:
    await check_async_artifact_store_conformance(lambda: to_async(InMemoryArtifactStore()))


async def test_async_artifact_store_conformance_json_file(tmp_path: Path) -> None:
    counter = {"n": 0}

    def make() -> AsyncArtifactStore:
        counter["n"] += 1
        return to_async(JsonFileArtifactStore(tmp_path / f"s{counter['n']}"))  # type: ignore[return-value]

    await check_async_artifact_store_conformance(make)


async def test_async_episodic_store_conformance() -> None:
    await check_async_episodic_store_conformance(lambda: to_async(InMemoryEpisodicStore()))


async def test_async_fact_store_conformance() -> None:
    await check_async_fact_store_conformance(lambda: to_async(InMemoryFactStore()))


# ---------------------------------------------------------------------------
# Bridge round-trip + detection
# ---------------------------------------------------------------------------


def test_to_sync_round_trips_through_to_async() -> None:
    """A sync backend -> async -> sync still satisfies the sync contract."""
    loop = _LoopThread()
    try:
        check_event_log_conformance(lambda: to_sync(to_async(InMemoryEventLog()), loop))
    finally:
        loop.close()


def test_is_async_store_detects_flavour() -> None:
    assert is_async_store(InMemoryEventLog()) is False
    assert is_async_store(to_async(InMemoryEventLog())) is True
    assert is_async_store(InMemoryArtifactStore()) is False
    assert is_async_store(to_async(InMemoryArtifactStore())) is True


def test_to_async_rejects_non_store() -> None:
    with pytest.raises(TypeError):
        to_async(object())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ContextManager accepts async backends
# ---------------------------------------------------------------------------


def _seed(log: EventLog) -> None:
    log.append(ContextItem(id="u1", kind=ItemKind.user_turn, text="how do I deploy the service"))
    log.append(ContextItem(id="t1", kind=ItemKind.tool_result, text="deployment succeeded"))


async def test_manager_with_async_stores_matches_sync() -> None:
    sync_mgr = ContextManager()
    _seed(sync_mgr.event_log)
    sync_pack = await sync_mgr.build(query="deploy")

    async_mgr = ContextManager(
        stores=StoreBundle(
            event_log=to_async(InMemoryEventLog()),
            artifact_store=to_async(InMemoryArtifactStore()),
            episodic_store=to_async(InMemoryEpisodicStore()),
            fact_store=to_async(InMemoryFactStore()),
        )
    )
    try:
        assert async_mgr._async_backed is True
        _seed(async_mgr.event_log)
        async_pack = await async_mgr.build(query="deploy")
        assert async_pack.prompt == sync_pack.prompt
    finally:
        async_mgr.close()


def test_manager_with_sync_stores_is_not_async_backed() -> None:
    mgr = ContextManager()
    assert mgr._async_backed is False
    assert mgr._store_loop is None
    mgr.close()  # no-op, must not raise


async def test_manager_async_episodic_and_facts_round_trip() -> None:
    mgr = ContextManager(
        stores=StoreBundle(
            episodic_store=to_async(InMemoryEpisodicStore()),
            fact_store=to_async(InMemoryFactStore()),
        )
    )
    try:
        mgr.episodic_store.add(Episode(episode_id="e1", summary="rotated credentials"))
        mgr.fact_store.put(Fact(fact_id="f1", key="env", value="prod"))
        assert mgr.episodic_store.get("e1") is not None
        assert mgr.fact_store.get("f1").value == "prod"
    finally:
        mgr.close()


# ---------------------------------------------------------------------------
# Non-blocking guarantee
# ---------------------------------------------------------------------------


class _SlowAsyncEventLog:
    """An :class:`AsyncEventLog` whose ``all()`` sleeps, to probe loop liveness."""

    def __init__(self, delay: float) -> None:
        self._inner = InMemoryEventLog()
        self._delay = delay

    async def append(self, item: ContextItem) -> None:
        self._inner.append(item)

    async def get(self, item_id: str) -> ContextItem:
        return self._inner.get(item_id)

    async def all(self) -> list[ContextItem]:
        await asyncio.sleep(self._delay)
        return self._inner.all()

    async def filter_by_kind(self, *kinds: ItemKind) -> list[ContextItem]:
        return self._inner.filter_by_kind(*kinds)

    async def tail(self, n: int) -> list[ContextItem]:
        return self._inner.tail(n)

    async def children(self, parent_id: str) -> list[ContextItem]:
        return self._inner.children(parent_id)

    async def parent(self, item_id: str) -> ContextItem | None:
        return self._inner.parent(item_id)

    async def query(
        self,
        kinds: list[ItemKind] | None = None,
        since: int | None = None,
        limit: int | None = None,
    ) -> list[ContextItem]:
        return self._inner.query(kinds, since, limit)

    async def count(self) -> int:
        return self._inner.count()

    async def close(self) -> None:
        self._inner.close()


async def test_async_build_does_not_block_event_loop() -> None:
    mgr = ContextManager(stores=StoreBundle(event_log=_SlowAsyncEventLog(delay=0.2)))
    try:
        _seed(mgr.event_log)

        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        tick_task = asyncio.create_task(ticker())
        start = time.perf_counter()
        await mgr.build(query="deploy")
        elapsed = time.perf_counter() - start
        tick_task.cancel()

        # The build awaited a ~0.2s store read; if the loop were blocked the
        # ticker could not have advanced. Require clear concurrent progress.
        assert elapsed >= 0.2
        assert ticks >= 5
    finally:
        mgr.close()

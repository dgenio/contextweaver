"""Tests for contextweaver.context.manager."""

from __future__ import annotations

import pytest

from contextweaver.context.manager import ContextManager
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.types import ContextItem, ContextPack, ItemKind, Phase


def _make_log(*texts: str) -> InMemoryEventLog:
    log = InMemoryEventLog()
    for i, text in enumerate(texts):
        log.append(ContextItem(id=f"item-{i}", kind=ItemKind.user_turn, text=text))
    return log


@pytest.mark.asyncio
async def test_build_returns_context_pack() -> None:
    log = _make_log("Hello world", "Search the database")
    mgr = ContextManager(event_log=log)
    pack = await mgr.build(phase=Phase.answer, query="database")
    assert isinstance(pack, ContextPack)
    assert pack.phase == Phase.answer


@pytest.mark.asyncio
async def test_build_empty_log() -> None:
    mgr = ContextManager()
    pack = await mgr.build(phase=Phase.route)
    assert pack.prompt == ""
    assert pack.stats.total_candidates == 0


@pytest.mark.asyncio
async def test_build_includes_items() -> None:
    log = _make_log("User asks about the database")
    mgr = ContextManager(event_log=log)
    pack = await mgr.build(phase=Phase.answer, query="database")
    assert "database" in pack.prompt.lower()


@pytest.mark.asyncio
async def test_build_stats_populated() -> None:
    log = _make_log("item one", "item two", "item three")
    mgr = ContextManager(event_log=log)
    pack = await mgr.build(phase=Phase.answer)
    assert pack.stats.total_candidates == 3


def test_build_sync() -> None:
    log = _make_log("synchronous test item")
    mgr = ContextManager(event_log=log)
    pack = mgr.build_sync(phase=Phase.answer)
    assert isinstance(pack, ContextPack)


@pytest.mark.asyncio
async def test_build_populates_envelopes_for_tool_results() -> None:
    log = InMemoryEventLog()
    log.append(ContextItem(id="u1", kind=ItemKind.user_turn, text="run query"))
    log.append(
        ContextItem(
            id="tr1",
            kind=ItemKind.tool_result,
            text="raw output: rows=[1,2,3]",
        )
    )
    mgr = ContextManager(event_log=log)
    pack = await mgr.build(phase=Phase.answer, query="query")
    assert len(pack.envelopes) == 1
    env = pack.envelopes[0]
    assert env.status == "ok"
    assert env.provenance["source_item_id"] == "tr1"


@pytest.mark.asyncio
async def test_build_sync_inside_running_loop() -> None:
    """build_sync() should work even when an event loop is already running."""
    log = _make_log("works inside async context")
    mgr = ContextManager(event_log=log)
    pack = mgr.build_sync(phase=Phase.answer)
    assert isinstance(pack, ContextPack)

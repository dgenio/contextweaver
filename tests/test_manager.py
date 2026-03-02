"""Tests for contextweaver.context.manager."""

from __future__ import annotations

import pytest

from contextweaver.context.manager import ContextManager
from contextweaver.store import StoreBundle
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


# ---------------------------------------------------------------------------
# Ingestion methods
# ---------------------------------------------------------------------------


def test_ingest_appends_to_event_log() -> None:
    mgr = ContextManager()
    item = ContextItem(id="u1", kind=ItemKind.user_turn, text="hello")
    mgr.ingest(item)
    assert mgr.event_log.count() == 1
    assert mgr.event_log.get("u1").text == "hello"


def test_ingest_sync() -> None:
    mgr = ContextManager()
    item = ContextItem(id="u1", kind=ItemKind.user_turn, text="hello")
    mgr.ingest_sync(item)
    assert mgr.event_log.count() == 1


def test_ingest_tool_result_small() -> None:
    mgr = ContextManager()
    item, env = mgr.ingest_tool_result(
        tool_call_id="tc1",
        raw_output="status: ok\ncount: 5",
        tool_name="db_query",
    )
    assert item.kind == ItemKind.tool_result
    assert item.parent_id == "tc1"
    assert env.status == "ok"
    assert mgr.event_log.count() == 1


def test_ingest_tool_result_large_triggers_firewall() -> None:
    mgr = ContextManager()
    large_output = "data: " + "x" * 3000
    item, env = mgr.ingest_tool_result(
        tool_call_id="tc2",
        raw_output=large_output,
        tool_name="big_tool",
        firewall_threshold=100,
    )
    assert item.artifact_ref is not None
    assert env.status == "ok"
    assert len(item.text) < len(large_output)
    assert mgr.event_log.count() == 1


def test_ingest_tool_result_sync() -> None:
    mgr = ContextManager()
    item, env = mgr.ingest_tool_result_sync(
        tool_call_id="tc3",
        raw_output="result: 42",
    )
    assert env.status == "ok"


# ---------------------------------------------------------------------------
# Fact / Episode helpers
# ---------------------------------------------------------------------------


def test_add_fact() -> None:
    mgr = ContextManager()
    mgr.add_fact("user_name", "Alice")
    facts = mgr.fact_store.all()
    assert len(facts) == 1
    assert facts[0].key == "user_name"
    assert facts[0].value == "Alice"


def test_add_fact_sync() -> None:
    mgr = ContextManager()
    mgr.add_fact_sync("key", "value")
    assert len(mgr.fact_store.all()) == 1


def test_add_episode() -> None:
    mgr = ContextManager()
    mgr.add_episode("ep1", "User searched for data")
    episodes = mgr.episodic_store.all()
    assert len(episodes) == 1
    assert episodes[0].episode_id == "ep1"


def test_add_episode_sync() -> None:
    mgr = ContextManager()
    mgr.add_episode_sync("ep1", "summary")
    assert len(mgr.episodic_store.all()) == 1


# ---------------------------------------------------------------------------
# Facts + episodic in build output
# ---------------------------------------------------------------------------


def test_build_includes_facts_in_prompt() -> None:
    mgr = ContextManager()
    mgr.add_fact("user_lang", "Python")
    mgr.ingest(ContextItem(id="u1", kind=ItemKind.user_turn, text="hello"))
    pack = mgr.build_sync(phase=Phase.answer)
    assert "user_lang" in pack.prompt
    assert "Python" in pack.prompt


def test_build_includes_episodic_in_prompt() -> None:
    mgr = ContextManager()
    mgr.add_episode("ep1", "Previously searched for billing data")
    mgr.ingest(ContextItem(id="u1", kind=ItemKind.user_turn, text="hello"))
    pack = mgr.build_sync(phase=Phase.answer)
    assert "billing" in pack.prompt.lower()


# ---------------------------------------------------------------------------
# StoreBundle constructor
# ---------------------------------------------------------------------------


def test_stores_bundle_constructor() -> None:
    log = InMemoryEventLog()
    log.append(ContextItem(id="u1", kind=ItemKind.user_turn, text="pre-loaded"))
    bundle = StoreBundle(event_log=log)
    mgr = ContextManager(stores=bundle)
    assert mgr.event_log.count() == 1


# ---------------------------------------------------------------------------
# Budget override
# ---------------------------------------------------------------------------


def test_build_with_budget_override() -> None:
    log = _make_log("item about database queries")
    mgr = ContextManager(event_log=log)
    pack = mgr.build_sync(phase=Phase.answer, budget_tokens=50)
    assert isinstance(pack, ContextPack)


# ---------------------------------------------------------------------------
# Per-phase build
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_route_phase() -> None:
    log = InMemoryEventLog()
    log.append(ContextItem(id="u1", kind=ItemKind.user_turn, text="search data"))
    mgr = ContextManager(event_log=log)
    pack = await mgr.build(phase=Phase.route, query="search")
    assert pack.phase == Phase.route


@pytest.mark.asyncio
async def test_build_call_phase() -> None:
    log = InMemoryEventLog()
    log.append(ContextItem(id="u1", kind=ItemKind.user_turn, text="search data"))
    mgr = ContextManager(event_log=log)
    pack = await mgr.build(phase=Phase.call, query="search")
    assert pack.phase == Phase.call


@pytest.mark.asyncio
async def test_build_interpret_phase() -> None:
    log = InMemoryEventLog()
    log.append(ContextItem(id="u1", kind=ItemKind.user_turn, text="query"))
    log.append(
        ContextItem(id="tr1", kind=ItemKind.tool_result, text="result data")
    )
    mgr = ContextManager(event_log=log)
    pack = await mgr.build(phase=Phase.interpret, query="interpret result")
    assert pack.phase == Phase.interpret

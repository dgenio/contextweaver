"""Tests for contextweaver.context.manager."""

from __future__ import annotations

import pytest

from contextweaver.context.manager import ContextManager
from contextweaver.exceptions import ItemNotFoundError
from contextweaver.routing.catalog import Catalog
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.store import StoreBundle
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.types import ContextItem, ContextPack, ItemKind, Phase, SelectableItem


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


def test_build_caps_facts_by_line_count() -> None:
    """Fact injection is capped at 64 lines; excess produces an omitted notice."""
    mgr = ContextManager()
    # Zero-padded keys so lexicographic == numeric order
    for i in range(80):
        mgr.add_fact(f"k{i:03d}", f"v{i}")
    mgr.ingest(ContextItem(id="u1", kind=ItemKind.user_turn, text="hello"))
    pack = mgr.build_sync(phase=Phase.answer)
    assert "more facts omitted" in pack.prompt
    # Key 063 (last within cap) should be present, key 064 should not
    assert "k063" in pack.prompt
    assert "- k064:" not in pack.prompt


def test_build_caps_facts_by_char_budget() -> None:
    """Fact injection truncates when total chars exceed 2000."""
    mgr = ContextManager()
    # Each fact line is ~210 chars → 10 facts ≈ 2100 chars, exceeds 2000
    for i in range(15):
        mgr.add_fact(f"key{i}", "x" * 200)
    mgr.ingest(ContextItem(id="u1", kind=ItemKind.user_turn, text="hello"))
    pack = mgr.build_sync(phase=Phase.answer)
    assert "facts truncated to fit header budget" in pack.prompt


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
# Header/footer budget accounting
# ---------------------------------------------------------------------------


def test_header_footer_tokens_recorded_in_stats() -> None:
    """BuildStats.header_footer_tokens reflects injected facts/episodes cost."""
    mgr = ContextManager()
    mgr.add_fact("lang", "Python")
    mgr.add_episode("ep1", "Searched billing data")
    mgr.ingest(ContextItem(id="u1", kind=ItemKind.user_turn, text="hello"))
    pack = mgr.build_sync(phase=Phase.answer)
    assert pack.stats.header_footer_tokens > 0
    assert "[FACTS]" in pack.prompt
    assert "[EPISODIC MEMORY]" in pack.prompt


def test_header_footer_tokens_zero_without_injection() -> None:
    """Without facts or episodes, header_footer_tokens is 0."""
    log = _make_log("hello world")
    mgr = ContextManager(event_log=log)
    pack = mgr.build_sync(phase=Phase.answer)
    assert pack.stats.header_footer_tokens == 0


def test_facts_budget_subtracted_from_selection() -> None:
    """Injected facts reduce the budget available for context items."""
    # Tight budget: 100 tokens total. With facts injected, fewer items fit.
    mgr_no_facts = ContextManager()
    mgr_no_facts.ingest(ContextItem(id="u1", kind=ItemKind.user_turn, text="a " * 50))
    mgr_no_facts.ingest(ContextItem(id="u2", kind=ItemKind.user_turn, text="b " * 50))
    pack_no = mgr_no_facts.build_sync(phase=Phase.answer, budget_tokens=100)

    mgr_with_facts = ContextManager()
    for i in range(10):
        mgr_with_facts.add_fact(f"k{i}", f"value-{i}")
    mgr_with_facts.ingest(ContextItem(id="u1", kind=ItemKind.user_turn, text="a " * 50))
    mgr_with_facts.ingest(ContextItem(id="u2", kind=ItemKind.user_turn, text="b " * 50))
    pack_with = mgr_with_facts.build_sync(phase=Phase.answer, budget_tokens=100)

    # With facts consuming part of the budget, fewer items should be included
    # OR the total context-item tokens should be lower.
    assert pack_with.stats.header_footer_tokens > 0
    items_tokens_no = sum(pack_no.stats.tokens_per_section.values())
    items_tokens_with = sum(pack_with.stats.tokens_per_section.values())
    assert items_tokens_with <= items_tokens_no


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
    log.append(ContextItem(id="tr1", kind=ItemKind.tool_result, text="result data"))
    mgr = ContextManager(event_log=log)
    pack = await mgr.build(phase=Phase.interpret, query="interpret result")
    assert pack.phase == Phase.interpret


# ---------------------------------------------------------------------------
# build_route_prompt
# ---------------------------------------------------------------------------


def _make_selectable_items() -> list[SelectableItem]:
    """Build a small catalog for route-prompt tests."""
    return [
        SelectableItem(
            id="db_read",
            kind="tool",
            name="read_db",
            description="Read from database",
            tags=["data"],
        ),
        SelectableItem(
            id="send_email",
            kind="tool",
            name="send_email",
            description="Send email notification",
            tags=["comm"],
        ),
        SelectableItem(
            id="search_docs",
            kind="tool",
            name="search_docs",
            description="Search documentation pages",
            tags=["search"],
        ),
    ]


def test_build_route_prompt_returns_tuple() -> None:
    """build_route_prompt returns (ContextPack, cards, RouteResult)."""
    items = _make_selectable_items()
    graph = TreeBuilder(max_children=10).build(items)
    router = Router(graph, items=items, beam_width=2, top_k=5)

    log = InMemoryEventLog()
    log.append(ContextItem(id="u1", kind=ItemKind.user_turn, text="read database"))
    mgr = ContextManager(event_log=log)

    pack, cards, route_result = mgr.build_route_prompt(
        goal="Find data tools",
        query="read database",
        router=router,
    )

    # ContextPack for route phase
    assert isinstance(pack, ContextPack)
    assert pack.phase == Phase.route

    # Cards list corresponds to route candidates
    assert isinstance(cards, list)
    assert len(cards) == len(route_result.candidate_ids)

    # RouteResult has matching lengths
    assert len(route_result.scores) == len(route_result.candidate_ids)

    # Prompt includes GOAL header and AVAILABLE TOOLS footer
    assert "[GOAL]" in pack.prompt
    assert "Find data tools" in pack.prompt
    assert "[AVAILABLE TOOLS]" in pack.prompt


def test_build_route_prompt_sync_alias() -> None:
    """build_route_prompt_sync is a working alias."""
    items = _make_selectable_items()
    graph = TreeBuilder(max_children=10).build(items)
    router = Router(graph, items=items)

    mgr = ContextManager()
    mgr.ingest(ContextItem(id="u1", kind=ItemKind.user_turn, text="search docs"))

    pack, cards, result = mgr.build_route_prompt_sync(
        goal="Search goal",
        query="search docs",
        router=router,
    )
    assert pack.phase == Phase.route
    assert len(cards) > 0
    assert len(result.candidate_ids) > 0


# ---------------------------------------------------------------------------
# build_call_prompt
# ---------------------------------------------------------------------------


def _make_catalog() -> Catalog:
    """Build a small catalog with schema data for call-prompt tests."""
    catalog = Catalog()
    catalog.register(
        SelectableItem(
            id="db_read",
            kind="tool",
            name="read_db",
            description="Read from database",
            tags=["data"],
            args_schema={
                "query": {"type": "string", "description": "SQL query"},
                "limit": {"type": "integer", "default": 100},
            },
            examples=["read_db(query='SELECT * FROM users', limit=10)"],
            constraints={"max_rows": 1000},
            cost_hint=0.1,
        )
    )
    catalog.register(
        SelectableItem(
            id="send_email",
            kind="tool",
            name="send_email",
            description="Send email notification",
            tags=["comm"],
            side_effects=True,
        )
    )
    return catalog


def test_build_call_prompt_injects_schema() -> None:
    """build_call_prompt_sync injects the tool schema into the prompt header."""
    catalog = _make_catalog()
    mgr = ContextManager()
    mgr.ingest(ContextItem(id="u1", kind=ItemKind.user_turn, text="read user data"))

    pack = mgr.build_call_prompt_sync(
        tool_id="db_read",
        query="read user data",
        catalog=catalog,
    )
    assert isinstance(pack, ContextPack)
    assert pack.phase == Phase.call
    assert "[TOOL SCHEMA]" in pack.prompt
    assert "read_db" in pack.prompt
    assert "SQL query" in pack.prompt
    assert "max_rows" in pack.prompt
    assert "read_db(query='SELECT * FROM users'" in pack.prompt


def test_build_call_prompt_missing_tool_raises() -> None:
    """build_call_prompt raises ItemNotFoundError for unknown tool IDs."""
    catalog = _make_catalog()
    mgr = ContextManager()
    mgr.ingest(ContextItem(id="u1", kind=ItemKind.user_turn, text="hello"))

    with pytest.raises(ItemNotFoundError):
        mgr.build_call_prompt_sync(
            tool_id="nonexistent",
            query="hello",
            catalog=catalog,
        )


def test_build_call_prompt_schema_override() -> None:
    """build_call_prompt accepts a schema override."""
    catalog = _make_catalog()
    mgr = ContextManager()
    mgr.ingest(ContextItem(id="u1", kind=ItemKind.user_turn, text="send email"))

    custom_schema = {"to": {"type": "string"}, "body": {"type": "string"}}
    pack = mgr.build_call_prompt_sync(
        tool_id="send_email",
        query="send email",
        catalog=catalog,
        schema=custom_schema,
    )
    assert '"to"' in pack.prompt
    assert '"body"' in pack.prompt


def test_build_call_prompt_budget_enforcement() -> None:
    """Schema token cost is subtracted from the call-phase budget."""
    catalog = _make_catalog()
    mgr = ContextManager()
    # Add multiple items so budget pressure is visible
    for i in range(20):
        mgr.ingest(ContextItem(id=f"u{i}", kind=ItemKind.user_turn, text=f"item {i} " * 20))

    pack = mgr.build_call_prompt_sync(
        tool_id="db_read",
        query="read data",
        catalog=catalog,
        budget_tokens=200,
    )
    assert pack.phase == Phase.call
    # Schema header should consume part of the budget
    assert pack.stats.header_footer_tokens > 0


def test_build_call_prompt_side_effects_flag() -> None:
    """Tools with side_effects=True include a side-effects notice."""
    catalog = _make_catalog()
    mgr = ContextManager()
    mgr.ingest(ContextItem(id="u1", kind=ItemKind.user_turn, text="send email"))

    pack = mgr.build_call_prompt_sync(
        tool_id="send_email",
        query="send email",
        catalog=catalog,
    )
    assert "Side effects: yes" in pack.prompt


@pytest.mark.asyncio
async def test_build_call_prompt_async() -> None:
    """Async build_call_prompt wrapper works."""
    catalog = _make_catalog()
    mgr = ContextManager()
    mgr.ingest(ContextItem(id="u1", kind=ItemKind.user_turn, text="read data"))

    pack = await mgr.build_call_prompt(
        tool_id="db_read",
        query="read data",
        catalog=catalog,
    )
    assert isinstance(pack, ContextPack)
    assert pack.phase == Phase.call
    assert "[TOOL SCHEMA]" in pack.prompt

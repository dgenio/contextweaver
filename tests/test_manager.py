"""Tests for contextweaver.context.manager -- ContextManager full flow, ingest_tool_result, build, facts, episodes."""

from __future__ import annotations

from contextweaver.context.manager import ContextManager, ContextPack
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.store.episodic import InMemoryEpisodicStore
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.store.facts import InMemoryFactStore
from contextweaver.types import BuildStats, ContextItem, ItemKind, Phase


class TestContextManager:
    """Tests for ContextManager."""

    async def test_ingest(self, context_manager: ContextManager) -> None:
        item = ContextItem(
            id="u1", kind=ItemKind.USER_TURN, text="Hello", token_estimate=2
        )
        await context_manager.ingest(item)
        assert await context_manager.event_log.count() == 1

    async def test_build_returns_context_pack(self, populated_manager: ContextManager) -> None:
        pack = await populated_manager.build(
            goal="Find unpaid invoices", phase=Phase.ANSWER
        )
        assert isinstance(pack, ContextPack)
        assert pack.phase == Phase.ANSWER
        assert isinstance(pack.stats, BuildStats)

    async def test_build_includes_items(self, populated_manager: ContextManager) -> None:
        pack = await populated_manager.build(
            goal="Find unpaid invoices", phase=Phase.ANSWER
        )
        assert len(pack.included_items) > 0
        assert pack.rendered_text != ""

    async def test_build_stats_populated(self, populated_manager: ContextManager) -> None:
        pack = await populated_manager.build(
            goal="invoices", phase=Phase.ANSWER
        )
        assert pack.stats.total_candidates > 0
        assert pack.stats.included_count + pack.stats.dropped_count == pack.stats.total_candidates

    async def test_facts_snapshot(self, populated_manager: ContextManager) -> None:
        pack = await populated_manager.build(
            goal="user info", phase=Phase.ANSWER
        )
        assert "user_name" in pack.facts_snapshot
        assert pack.facts_snapshot["user_name"] == "Alice"
        assert "account_id" in pack.facts_snapshot

    async def test_episodic_summaries(self, populated_manager: ContextManager) -> None:
        pack = await populated_manager.build(
            goal="recent context", phase=Phase.ANSWER
        )
        assert len(pack.episodic_summaries) > 0
        assert "unpaid invoices" in pack.episodic_summaries[0]

    async def test_ingest_tool_result_small(self) -> None:
        mgr = ContextManager()
        item, envelope = await mgr.ingest_tool_result(
            tool_call_id="tc1",
            raw_output="status: ok",
            tool_name="search",
        )
        assert item.kind == ItemKind.TOOL_RESULT
        assert envelope.status == "ok"
        assert await mgr.event_log.count() == 1

    async def test_ingest_tool_result_large(self) -> None:
        mgr = ContextManager()
        large_output = "X" * 5000
        item, envelope = await mgr.ingest_tool_result(
            tool_call_id="tc2",
            raw_output=large_output,
            tool_name="big_tool",
            firewall_threshold=2000,
        )
        assert item.artifact_ref is not None
        assert len(envelope.artifacts) == 1
        assert len(mgr.artifact_store.list_refs()) == 1

    async def test_add_fact_and_episode(self) -> None:
        mgr = ContextManager()
        await mgr.add_fact("key1", "value1")
        result = await mgr.fact_store.get("key1")
        assert result is not None
        assert result[0] == "value1"

        await mgr.add_episode("ep1", "Episode summary")
        summary, _ = await mgr.episodic_store.get("ep1")
        assert summary == "Episode summary"

    async def test_build_route_phase(self, populated_manager: ContextManager) -> None:
        pack = await populated_manager.build(
            goal="Find invoices", phase=Phase.ROUTE
        )
        # ROUTE phase only includes USER_TURN, PLAN_STATE, POLICY
        included_kinds = {item.kind for item in pack.included_items}
        assert ItemKind.TOOL_RESULT not in included_kinds

    async def test_build_with_custom_budget(self, populated_manager: ContextManager) -> None:
        pack = await populated_manager.build(
            goal="invoices", phase=Phase.ANSWER, budget_tokens=50
        )
        assert pack.budget_total == 50
        assert pack.budget_used <= 50

    async def test_default_stores_created(self) -> None:
        mgr = ContextManager()
        assert isinstance(mgr.event_log, InMemoryEventLog)
        assert isinstance(mgr.artifact_store, InMemoryArtifactStore)
        assert isinstance(mgr.episodic_store, InMemoryEpisodicStore)
        assert isinstance(mgr.fact_store, InMemoryFactStore)

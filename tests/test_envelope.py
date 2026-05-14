"""Tests for contextweaver.envelope."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from contextweaver.envelope import (
    BuildStats,
    ChoiceCard,
    ContextPack,
    ResultEnvelope,
    RoutingDecision,
)
from contextweaver.types import ArtifactRef, Phase, ViewSpec

# ---------------------------------------------------------------------------
# ResultEnvelope
# ---------------------------------------------------------------------------


def test_result_envelope_defaults() -> None:
    env = ResultEnvelope(status="ok", summary="done")
    assert env.facts == []
    assert env.artifacts == []
    assert env.views == []
    assert env.provenance == {}


def test_result_envelope_roundtrip() -> None:
    ref = ArtifactRef(handle="h1", media_type="text/plain", size_bytes=42, label="data")
    vs = ViewSpec(view_id="v1", label="table", selector={"cols": ["a"]}, artifact_ref=ref)
    env = ResultEnvelope(
        status="partial",
        summary="partial result",
        facts=["count: 10", "status: warning"],
        artifacts=[ref],
        views=[vs],
        provenance={"tool": "db", "run_id": "r1"},
    )
    d = env.to_dict()
    restored = ResultEnvelope.from_dict(d)
    assert restored.status == "partial"
    assert restored.summary == "partial result"
    assert restored.facts == ["count: 10", "status: warning"]
    assert len(restored.artifacts) == 1
    assert restored.artifacts[0].handle == "h1"
    assert len(restored.views) == 1
    assert restored.views[0].view_id == "v1"
    assert restored.provenance == {"tool": "db", "run_id": "r1"}


def test_result_envelope_error_status() -> None:
    env = ResultEnvelope(status="error", summary="failed to run tool")
    d = env.to_dict()
    assert d["status"] == "error"
    restored = ResultEnvelope.from_dict(d)
    assert restored.status == "error"


# ---------------------------------------------------------------------------
# BuildStats
# ---------------------------------------------------------------------------


def test_build_stats_defaults() -> None:
    bs = BuildStats()
    assert bs.tokens_per_section == {}
    assert bs.total_candidates == 0
    assert bs.included_count == 0
    assert bs.dropped_count == 0
    assert bs.dropped_reasons == {}
    assert bs.dedup_removed == 0
    assert bs.dependency_closures == 0
    assert bs.header_footer_tokens == 0


def test_build_stats_roundtrip() -> None:
    bs = BuildStats(
        tokens_per_section={"system": 200, "history": 400},
        total_candidates=50,
        included_count=30,
        dropped_count=20,
        dropped_reasons={"budget": 15, "policy": 5},
        dedup_removed=3,
        dependency_closures=2,
        header_footer_tokens=42,
    )
    d = bs.to_dict()
    restored = BuildStats.from_dict(d)
    assert restored.tokens_per_section == {"system": 200, "history": 400}
    assert restored.total_candidates == 50
    assert restored.included_count == 30
    assert restored.dropped_count == 20
    assert restored.dropped_reasons == {"budget": 15, "policy": 5}
    assert restored.dedup_removed == 3
    assert restored.dependency_closures == 2
    assert restored.header_footer_tokens == 42


def test_build_stats_from_empty_dict() -> None:
    bs = BuildStats.from_dict({})
    assert bs.total_candidates == 0
    assert bs.tokens_per_section == {}


# ---------------------------------------------------------------------------
# ContextPack
# ---------------------------------------------------------------------------


def test_context_pack_defaults() -> None:
    pack = ContextPack(prompt="hello")
    assert pack.phase == Phase.answer
    assert pack.envelopes == []
    assert pack.stats.total_candidates == 0


def test_context_pack_roundtrip() -> None:
    env = ResultEnvelope(status="ok", summary="done", facts=["x=1"])
    bs = BuildStats(total_candidates=10, included_count=8, dropped_count=2)
    pack = ContextPack(prompt="context here", stats=bs, phase=Phase.route, envelopes=[env])
    d = pack.to_dict()
    restored = ContextPack.from_dict(d)
    assert restored.prompt == "context here"
    assert restored.phase == Phase.route
    assert restored.stats.total_candidates == 10
    assert len(restored.envelopes) == 1
    assert restored.envelopes[0].summary == "done"


def test_context_pack_multiple_envelopes() -> None:
    envs = [ResultEnvelope(status="ok", summary=f"result {i}") for i in range(3)]
    pack = ContextPack(prompt="multi", envelopes=envs)
    d = pack.to_dict()
    restored = ContextPack.from_dict(d)
    assert len(restored.envelopes) == 3
    assert restored.envelopes[2].summary == "result 2"


def test_context_pack_from_partial_dict() -> None:
    pack = ContextPack.from_dict({"prompt": "minimal"})
    assert pack.prompt == "minimal"
    assert pack.phase == Phase.answer
    assert pack.envelopes == []


# ---------------------------------------------------------------------------
# ChoiceCard
# ---------------------------------------------------------------------------


def test_choice_card_defaults() -> None:
    card = ChoiceCard(id="c1", name="search", description="Search tool")
    assert card.tags == []
    assert card.cost_hint == 0.0
    assert card.side_effects is False


def test_choice_card_roundtrip() -> None:
    card = ChoiceCard(
        id="c1",
        name="search_db",
        description="Search the database",
        tags=["data", "search"],
        cost_hint=0.3,
        side_effects=True,
    )
    d = card.to_dict()
    restored = ChoiceCard.from_dict(d)
    assert restored.id == "c1"
    assert restored.name == "search_db"
    assert restored.description == "Search the database"
    assert restored.tags == ["data", "search"]
    assert restored.cost_hint == 0.3
    assert restored.side_effects is True


def test_choice_card_from_partial_dict() -> None:
    card = ChoiceCard.from_dict({"id": "x", "name": "n", "description": "d"})
    assert card.tags == []
    assert card.cost_hint == 0.0
    assert card.side_effects is False


# ---------------------------------------------------------------------------
# RoutingDecision (issue #151)
# ---------------------------------------------------------------------------


def test_routing_decision_defaults() -> None:
    card = ChoiceCard(id="c1", name="n", description="d")
    ts = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)
    rd = RoutingDecision(id="rd-1", choice_cards=[card], timestamp=ts)
    assert rd.id == "rd-1"
    assert rd.choice_cards == [card]
    assert rd.timestamp == ts
    assert rd.selected_item_id is None
    assert rd.selected_card_id is None
    assert rd.context_summary is None
    assert rd.metadata == {}


def test_routing_decision_to_dict_omits_none_optionals() -> None:
    card = ChoiceCard(id="c1", name="n", description="d")
    ts = datetime(2026, 5, 14, tzinfo=timezone.utc)
    rd = RoutingDecision(id="rd-1", choice_cards=[card], timestamp=ts)
    d = rd.to_dict()
    assert d["id"] == "rd-1"
    assert d["timestamp"] == "2026-05-14T00:00:00+00:00"
    assert d["metadata"] == {}
    # JSON Schema rejects null for these — they must be absent, not None.
    assert "selected_item_id" not in d
    assert "selected_card_id" not in d
    assert "context_summary" not in d


def test_routing_decision_roundtrip() -> None:
    cards = [
        ChoiceCard(id="t1", name="search", description="Search", score=0.9),
        ChoiceCard(id="t2", name="filter", description="Filter", tags=["query"]),
    ]
    ts = datetime(2026, 5, 14, 9, 30, 15, tzinfo=timezone.utc)
    rd = RoutingDecision(
        id="rd-abc",
        choice_cards=cards,
        timestamp=ts,
        selected_item_id="t1",
        selected_card_id="t1",
        context_summary="user asked about reports",
        metadata={"trace_id": "abc-123"},
    )
    d = rd.to_dict()
    restored = RoutingDecision.from_dict(d)
    assert restored.id == "rd-abc"
    assert len(restored.choice_cards) == 2
    assert restored.choice_cards[0].id == "t1"
    assert restored.choice_cards[0].score == 0.9
    assert restored.choice_cards[1].tags == ["query"]
    assert restored.timestamp == ts
    assert restored.selected_item_id == "t1"
    assert restored.selected_card_id == "t1"
    assert restored.context_summary == "user asked about reports"
    assert restored.metadata == {"trace_id": "abc-123"}


def test_routing_decision_from_dict_accepts_datetime_object() -> None:
    card = ChoiceCard(id="t1", name="n", description="d")
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rd = RoutingDecision.from_dict(
        {"id": "rd-1", "choice_cards": [card.to_dict()], "timestamp": ts}
    )
    assert rd.timestamp == ts


def test_routing_decision_from_dict_naive_timestamp_assumed_utc() -> None:
    card = ChoiceCard(id="t1", name="n", description="d")
    rd = RoutingDecision.from_dict(
        {"id": "rd-1", "choice_cards": [card.to_dict()], "timestamp": "2026-05-14T00:00:00"}
    )
    assert rd.timestamp.tzinfo is not None
    assert rd.timestamp.tzinfo.utcoffset(rd.timestamp) == timezone.utc.utcoffset(rd.timestamp)


def test_routing_decision_from_partial_dict() -> None:
    rd = RoutingDecision.from_dict({"id": "rd-1"})
    assert rd.id == "rd-1"
    assert rd.choice_cards == []
    assert rd.timestamp.tzinfo is not None
    assert rd.metadata == {}


def test_routing_decision_metadata_is_copied_not_aliased() -> None:
    card = ChoiceCard(id="t1", name="n", description="d")
    original_meta = {"trace_id": "abc"}
    rd = RoutingDecision(
        id="rd-1",
        choice_cards=[card],
        timestamp=datetime.now(timezone.utc),
        metadata=original_meta,
    )
    rd.to_dict()["metadata"]["trace_id"] = "MUTATED"
    assert rd.metadata == {"trace_id": "abc"}
    assert original_meta == {"trace_id": "abc"}


# ---------------------------------------------------------------------------
# RouteResult.to_routing_decision (issue #151)
# ---------------------------------------------------------------------------


def test_route_result_to_routing_decision_basic() -> None:
    from contextweaver.routing.router import RouteResult
    from contextweaver.types import SelectableItem

    items = [
        SelectableItem(id="t1", kind="tool", name="search", description="Search"),
        SelectableItem(id="t2", kind="tool", name="filter", description="Filter"),
    ]
    result = RouteResult(
        candidate_items=items,
        candidate_ids=["t1", "t2"],
        scores=[0.85, 0.42],
    )
    rd = result.to_routing_decision(decision_id="rd-test")
    assert rd.id == "rd-test"
    assert len(rd.choice_cards) == 2
    assert rd.choice_cards[0].id == "t1"
    assert rd.choice_cards[0].score == 0.85
    assert rd.choice_cards[1].score == 0.42
    assert rd.selected_item_id is None
    assert rd.timestamp.tzinfo is not None


def test_route_result_to_routing_decision_auto_id_is_unique() -> None:
    from contextweaver.routing.router import RouteResult
    from contextweaver.types import SelectableItem

    items = [SelectableItem(id="t1", kind="tool", name="n", description="d")]
    result = RouteResult(candidate_items=items, candidate_ids=["t1"], scores=[1.0])
    rd1 = result.to_routing_decision()
    rd2 = result.to_routing_decision()
    assert rd1.id != rd2.id
    assert rd1.id.startswith("rd-")
    assert rd2.id.startswith("rd-")


def test_route_result_to_routing_decision_with_selection() -> None:
    from contextweaver.routing.router import RouteResult
    from contextweaver.types import SelectableItem

    items = [
        SelectableItem(id="t1", kind="tool", name="search", description="Search"),
        SelectableItem(id="t2", kind="tool", name="filter", description="Filter"),
    ]
    result = RouteResult(
        candidate_items=items,
        candidate_ids=["t1", "t2"],
        scores=[0.85, 0.42],
    )
    rd = result.to_routing_decision(
        decision_id="rd-1",
        selected_item_id="t1",
        context_summary="user wanted search",
    )
    assert rd.selected_item_id == "t1"
    # selected_card_id is auto-resolved from selected_item_id.
    assert rd.selected_card_id == "t1"
    assert rd.context_summary == "user wanted search"


def test_route_result_to_routing_decision_preserves_router_diagnostics() -> None:
    from contextweaver.routing.router import RouteResult
    from contextweaver.types import SelectableItem

    items = [SelectableItem(id="t1", kind="tool", name="n", description="d")]
    result = RouteResult(
        candidate_items=items,
        candidate_ids=["t1"],
        scores=[0.9],
        is_ambiguous=True,
        excluded_count=3,
        gated_count=2,
        context_hints=["billing"],
        context_boost_applied=True,
        clarifying_question="Did you mean billing or analytics?",
    )
    rd = result.to_routing_decision()
    cw_meta = rd.metadata["contextweaver"]
    assert cw_meta["is_ambiguous"] is True
    assert cw_meta["excluded_count"] == 3
    assert cw_meta["gated_count"] == 2
    assert cw_meta["context_boost_applied"] is True
    assert cw_meta["context_hints"] == ["billing"]
    assert cw_meta["clarifying_question"] == "Did you mean billing or analytics?"


def test_route_result_to_routing_decision_empty_raises() -> None:
    from contextweaver.exceptions import RouteError
    from contextweaver.routing.router import RouteResult

    result = RouteResult()
    with pytest.raises(RouteError, match="at least one candidate"):
        result.to_routing_decision()


def test_route_result_to_routing_decision_now_override() -> None:
    from contextweaver.routing.router import RouteResult
    from contextweaver.types import SelectableItem

    items = [SelectableItem(id="t1", kind="tool", name="n", description="d")]
    result = RouteResult(candidate_items=items, candidate_ids=["t1"], scores=[1.0])
    fixed = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rd = result.to_routing_decision(now=fixed)
    assert rd.timestamp == fixed


def test_route_result_to_routing_decision_naive_now_assumed_utc() -> None:
    from contextweaver.routing.router import RouteResult
    from contextweaver.types import SelectableItem

    items = [SelectableItem(id="t1", kind="tool", name="n", description="d")]
    result = RouteResult(candidate_items=items, candidate_ids=["t1"], scores=[1.0])
    naive = datetime(2026, 1, 1)
    rd = result.to_routing_decision(now=naive)
    assert rd.timestamp.tzinfo is not None

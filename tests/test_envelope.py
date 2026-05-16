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
# BuildStats.prompt_tokens (issue #106)
# ---------------------------------------------------------------------------


def test_build_stats_prompt_tokens_property() -> None:
    bs = BuildStats(
        tokens_per_section={"system": 200, "history": 400, "facts": 50},
        header_footer_tokens=42,
    )
    # Single source of truth: sum of sections + header/footer.
    assert bs.prompt_tokens == 200 + 400 + 50 + 42


def test_build_stats_prompt_tokens_empty() -> None:
    assert BuildStats().prompt_tokens == 0


# ---------------------------------------------------------------------------
# BuildStats.report() / report_dict() (issue #106)
# ---------------------------------------------------------------------------


def _sample_stats() -> BuildStats:
    return BuildStats(
        tokens_per_section={"facts": 180, "history": 1200, "tool_results": 1800, "episodes": 320},
        total_candidates=24,
        included_count=12,
        dropped_count=8,
        dropped_reasons={"budget_exceeded": 5, "sensitivity": 2, "dedup": 1},
        dedup_removed=4,
        dependency_closures=2,
        header_footer_tokens=0,
    )


def test_build_stats_report_text_contains_sections() -> None:
    out = _sample_stats().report(format="text", phase="answer", budget=4000)
    assert "Context Build Report" in out
    assert "Phase:  answer" in out
    assert "Budget: 4000" in out
    assert "Generated:    24" in out
    assert "Included:     12" in out
    assert "Dropped:      8" in out
    # token-section table row
    assert "tool_results" in out and "1800" in out
    # drop reasons section
    assert "budget_exceeded: 5" in out


def test_build_stats_report_deterministic() -> None:
    stats = _sample_stats()
    a = stats.report(format="text", phase="answer", budget=4000)
    b = stats.report(format="text", phase="answer", budget=4000)
    assert a == b
    # Sorted section order — independent of insertion order — yields stable bytes
    # even when ``tokens_per_section`` is built from a different insertion order.
    shuffled = BuildStats(
        tokens_per_section={"tool_results": 1800, "facts": 180, "episodes": 320, "history": 1200},
        total_candidates=stats.total_candidates,
        included_count=stats.included_count,
        dropped_count=stats.dropped_count,
        dropped_reasons=stats.dropped_reasons,
        dedup_removed=stats.dedup_removed,
        dependency_closures=stats.dependency_closures,
        header_footer_tokens=stats.header_footer_tokens,
    )
    assert shuffled.report(format="text", phase="answer", budget=4000) == a


def test_build_stats_report_recommends_when_section_over_budget() -> None:
    stats = BuildStats(
        tokens_per_section={"history": 2500},  # 62.5 % of 4000 budget
        total_candidates=10,
        included_count=8,
    )
    out = stats.report(format="text", phase="answer", budget=4000)
    assert "history" in out
    assert "lowering firewall threshold" in out


def test_build_stats_report_recommends_headroom_when_efficient() -> None:
    stats = BuildStats(tokens_per_section={"facts": 1000}, included_count=5, total_candidates=5)
    out = stats.report(format="text", phase="answer", budget=4000)
    assert "budget headroom" in out


def test_build_stats_report_warns_when_over_budget() -> None:
    stats = BuildStats(tokens_per_section={"history": 5000})
    out = stats.report(format="text", phase="answer", budget=4000)
    assert "over budget" in out


def test_build_stats_report_no_budget_skips_percentages() -> None:
    out = _sample_stats().report(format="text", phase="answer", budget=None)
    assert "% Budget" not in out
    # Section table still present without percentage columns.
    assert "1800" in out


def test_build_stats_report_rich_has_markup() -> None:
    out = _sample_stats().report(format="rich", phase="answer", budget=4000)
    assert "[bold cyan]Context Build Report[/bold cyan]" in out
    assert "[bold]Candidates[/bold]" in out
    # Determinism applies to rich format too
    assert out == _sample_stats().report(format="rich", phase="answer", budget=4000)


def test_build_stats_report_dict_versioned() -> None:
    payload = _sample_stats().report_dict(phase="answer", budget=4000)
    assert payload["version"] == 1
    assert payload["phase"] == "answer"
    assert payload["budget"] == 4000
    assert payload["prompt_tokens"] == 180 + 1200 + 1800 + 320
    assert payload["candidates"] == {
        "total": 24,
        "included": 12,
        "dropped": 8,
        "deduplicated": 4,
        "dependency_closures": 2,
    }
    # dropped_reasons sorted deterministically
    assert list(payload["dropped_reasons"].keys()) == ["budget_exceeded", "dedup", "sensitivity"]


def test_build_stats_report_dict_empty_recommendations_without_budget() -> None:
    payload = _sample_stats().report_dict(phase="answer", budget=None)
    assert payload["recommendations"] == []


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


def test_choice_card_rejects_unknown_kind() -> None:
    """`kind` is constrained to the gateway-spec enum at runtime, not only by mypy."""
    with pytest.raises(ValueError, match="ChoiceCard.kind must be one of"):
        ChoiceCard(id="c1", name="n", description="d", kind="bogus")  # type: ignore[arg-type]


def test_choice_card_from_dict_rejects_unknown_kind() -> None:
    """`from_dict` must reject an out-of-enum `kind` value rather than silently accept it."""
    with pytest.raises(ValueError, match="ChoiceCard.kind must be one of"):
        ChoiceCard.from_dict({"id": "c1", "name": "n", "description": "d", "kind": "bogus"})


def test_choice_card_accepts_all_documented_kinds() -> None:
    """Every value of the `Literal[...]` enum constructs successfully."""
    for kind in ("tool", "agent", "skill", "internal"):
        card = ChoiceCard(id=f"c-{kind}", name="n", description="d", kind=kind)  # type: ignore[arg-type]
        assert card.kind == kind


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


def test_route_result_to_routing_decision_preserves_caller_supplied_metadata_namespace() -> None:
    """Regression for the setdefault→merge fix (PR #201 Phase 1).

    A caller may supply their own ``metadata['contextweaver']`` dict for tracing
    or correlation. The helper must merge the router diagnostics into that
    existing namespace rather than dropping them via ``setdefault``.
    """
    from contextweaver.routing.router import RouteResult
    from contextweaver.types import SelectableItem

    items = [SelectableItem(id="t1", kind="tool", name="n", description="d")]
    result = RouteResult(
        candidate_items=items,
        candidate_ids=["t1"],
        scores=[1.0],
        is_ambiguous=True,
        excluded_count=2,
    )
    rd = result.to_routing_decision(
        metadata={"contextweaver": {"trace_id": "abc-123", "request_id": "req-1"}}
    )
    cw_meta = rd.metadata["contextweaver"]
    # Caller keys preserved
    assert cw_meta["trace_id"] == "abc-123"
    assert cw_meta["request_id"] == "req-1"
    # Router diagnostics merged in
    assert cw_meta["is_ambiguous"] is True
    assert cw_meta["excluded_count"] == 2


def test_route_result_to_routing_decision_caller_metadata_wins_on_key_collision() -> None:
    """If the caller pre-populates a diagnostic key (e.g. ``is_ambiguous``),
    keep the caller's value — they presumably know what they're doing.
    """
    from contextweaver.routing.router import RouteResult
    from contextweaver.types import SelectableItem

    items = [SelectableItem(id="t1", kind="tool", name="n", description="d")]
    result = RouteResult(
        candidate_items=items,
        candidate_ids=["t1"],
        scores=[1.0],
        is_ambiguous=True,  # router says True
    )
    rd = result.to_routing_decision(
        metadata={"contextweaver": {"is_ambiguous": False}}  # caller forces False
    )
    assert rd.metadata["contextweaver"]["is_ambiguous"] is False


def test_routing_decision_from_dict_parses_z_suffix_timestamp() -> None:
    """Regression for the RFC 3339 Z-suffix normalisation (PR #201 Phase 1).

    Python 3.10's ``datetime.fromisoformat`` does not accept the ``Z`` suffix
    that schema-valid ``date-time`` payloads commonly use. The helper must
    normalise ``Z`` → ``+00:00`` so spec-shaped payloads parse across the
    full 3.10 / 3.11 / 3.12 matrix.
    """
    rd = RoutingDecision.from_dict(
        {
            "id": "rd-z",
            "choice_cards": [
                {"id": "t1", "name": "n", "description": "d"},
            ],
            "timestamp": "2026-05-14T00:00:00Z",
        }
    )
    assert rd.timestamp.tzinfo is not None
    assert rd.timestamp == datetime(2026, 5, 14, 0, 0, 0, tzinfo=timezone.utc)


def test_route_result_to_routing_decision_preserves_more_than_twenty_candidates() -> None:
    """Regression for the max_choices truncation guard (PR #201 Phase 1).

    ``make_choice_cards`` defaults to ``max_choices=20``. A router configured
    with ``top_k > 20`` must round-trip every candidate into the
    ``RoutingDecision.choice_cards`` list — no silent truncation.
    """
    from contextweaver.routing.router import RouteResult
    from contextweaver.types import SelectableItem

    items = [
        SelectableItem(id=f"t{i}", kind="tool", name=f"n{i}", description="d") for i in range(25)
    ]
    result = RouteResult(
        candidate_items=items,
        candidate_ids=[item.id for item in items],
        scores=[1.0 - i * 0.01 for i in range(25)],
    )
    rd = result.to_routing_decision()
    assert len(rd.choice_cards) == 25
    assert [c.id for c in rd.choice_cards] == [f"t{i}" for i in range(25)]

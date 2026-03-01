"""Tests for contextweaver.envelope."""

from __future__ import annotations

from contextweaver.envelope import (
    BuildStats,
    ChoiceCard,
    ContextPack,
    ResultEnvelope,
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


def test_build_stats_roundtrip() -> None:
    bs = BuildStats(
        tokens_per_section={"system": 200, "history": 400},
        total_candidates=50,
        included_count=30,
        dropped_count=20,
        dropped_reasons={"budget": 15, "policy": 5},
        dedup_removed=3,
        dependency_closures=2,
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
    envs = [
        ResultEnvelope(status="ok", summary=f"result {i}")
        for i in range(3)
    ]
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

"""Tests for contextweaver.types."""

from __future__ import annotations

from contextweaver.types import (
    ArtifactRef,
    BuildStats,
    ChoiceCard,
    ContextItem,
    ContextPack,
    ItemKind,
    Phase,
    ResultEnvelope,
    SelectableItem,
    Sensitivity,
    ViewSpec,
)


def test_sensitivity_values() -> None:
    assert Sensitivity.public.value == "public"
    assert Sensitivity.restricted.value == "restricted"


def test_item_kind_values() -> None:
    assert ItemKind.user_turn.value == "user_turn"
    assert ItemKind.tool_result.value == "tool_result"


def test_phase_values() -> None:
    assert Phase.route.value == "route"
    assert Phase.answer.value == "answer"


def test_selectable_item_roundtrip() -> None:
    item = SelectableItem(
        id="t1",
        kind="tool",
        name="search_db",
        description="Search the database",
        tags=["data", "search"],
        namespace="db",
        args_schema={"q": {"type": "string"}},
        side_effects=False,
        cost_hint=0.5,
    )
    d = item.to_dict()
    restored = SelectableItem.from_dict(d)
    assert restored.id == item.id
    assert restored.tags == item.tags
    assert restored.cost_hint == item.cost_hint


def test_artifact_ref_roundtrip() -> None:
    ref = ArtifactRef(handle="h1", media_type="text/plain", size_bytes=100, label="test")
    assert ArtifactRef.from_dict(ref.to_dict()).handle == "h1"


def test_context_item_roundtrip() -> None:
    item = ContextItem(
        id="ci1",
        kind=ItemKind.user_turn,
        text="Hello",
        token_estimate=5,
        parent_id=None,
    )
    d = item.to_dict()
    restored = ContextItem.from_dict(d)
    assert restored.kind == ItemKind.user_turn
    assert restored.text == "Hello"


def test_context_item_with_artifact_ref() -> None:
    ref = ArtifactRef(handle="h2", media_type="application/json", size_bytes=200)
    item = ContextItem(id="ci2", kind=ItemKind.tool_result, text="result", artifact_ref=ref)
    restored = ContextItem.from_dict(item.to_dict())
    assert restored.artifact_ref is not None
    assert restored.artifact_ref.handle == "h2"


def test_view_spec_roundtrip() -> None:
    vs = ViewSpec(view_id="v1", label="table", selector={"cols": ["a", "b"]})
    restored = ViewSpec.from_dict(vs.to_dict())
    assert restored.view_id == "v1"
    assert restored.selector == {"cols": ["a", "b"]}


def test_result_envelope_roundtrip() -> None:
    env = ResultEnvelope(
        status="ok",
        summary="Done",
        facts=["x: 1"],
        provenance={"tool": "db"},
    )
    restored = ResultEnvelope.from_dict(env.to_dict())
    assert restored.status == "ok"
    assert restored.facts == ["x: 1"]


def test_build_stats_roundtrip() -> None:
    stats = BuildStats(total_candidates=10, included_count=5, dropped_count=5)
    restored = BuildStats.from_dict(stats.to_dict())
    assert restored.total_candidates == 10


def test_context_pack_roundtrip() -> None:
    pack = ContextPack(prompt="hello", phase=Phase.route)
    restored = ContextPack.from_dict(pack.to_dict())
    assert restored.phase == Phase.route
    assert restored.prompt == "hello"


def test_choice_card_roundtrip() -> None:
    card = ChoiceCard(id="c1", name="search", description="Search tool", tags=["search"])
    restored = ChoiceCard.from_dict(card.to_dict())
    assert restored.id == "c1"
    assert restored.tags == ["search"]

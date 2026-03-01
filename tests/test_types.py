"""Tests for contextweaver.types -- serde for all types, enums, ToolCard alias, BuildStats."""

from __future__ import annotations

from contextweaver.types import (
    ArtifactRef,
    BuildStats,
    ContextItem,
    ItemKind,
    Phase,
    ResultEnvelope,
    SelectableItem,
    Sensitivity,
    ToolCard,
    ViewSpec,
)


class TestEnums:
    """Tests for enum values and string representation."""

    def test_phase_values(self) -> None:
        assert Phase.ROUTE.value == "route"
        assert Phase.CALL.value == "call"
        assert Phase.INTERPRET.value == "interpret"
        assert Phase.ANSWER.value == "answer"

    def test_item_kind_values(self) -> None:
        assert ItemKind.USER_TURN.value == "user_turn"
        assert ItemKind.AGENT_MSG.value == "agent_msg"
        assert ItemKind.TOOL_CALL.value == "tool_call"
        assert ItemKind.TOOL_RESULT.value == "tool_result"
        assert ItemKind.DOC_SNIPPET.value == "doc_snippet"
        assert ItemKind.MEMORY_FACT.value == "memory_fact"
        assert ItemKind.PLAN_STATE.value == "plan_state"
        assert ItemKind.POLICY.value == "policy"
        assert len(ItemKind) == 8

    def test_sensitivity_values(self) -> None:
        assert Sensitivity.PUBLIC.value == "public"
        assert Sensitivity.INTERNAL.value == "internal"
        assert Sensitivity.CONFIDENTIAL.value == "confidential"
        assert Sensitivity.RESTRICTED.value == "restricted"

    def test_enum_from_string(self) -> None:
        assert Phase("route") is Phase.ROUTE
        assert ItemKind("tool_call") is ItemKind.TOOL_CALL
        assert Sensitivity("confidential") is Sensitivity.CONFIDENTIAL


class TestSelectableItem:
    """Tests for SelectableItem serde."""

    def test_round_trip(self) -> None:
        item = SelectableItem(
            id="billing.search",
            kind="tool",
            name="billing.search",
            description="Search invoices",
            tags=["billing", "search"],
            namespace="billing",
            args_schema={"type": "object"},
            side_effects=True,
            cost_hint="medium",
            metadata={"source": "test"},
        )
        d = item.to_dict()
        restored = SelectableItem.from_dict(d)
        assert restored.id == item.id
        assert restored.kind == item.kind
        assert restored.tags == item.tags
        assert restored.args_schema == {"type": "object"}
        assert restored.side_effects is True
        assert restored.cost_hint == "medium"

    def test_defaults(self) -> None:
        item = SelectableItem(id="t1", kind="tool", name="t1", description="desc")
        assert item.tags == []
        assert item.namespace == ""
        assert item.args_schema is None
        assert item.side_effects is False
        assert item.cost_hint == "low"

    def test_from_dict_missing_optional_fields(self) -> None:
        d = {"id": "x", "kind": "tool", "name": "x", "description": "d"}
        item = SelectableItem.from_dict(d)
        assert item.namespace == ""
        assert item.args_schema is None
        assert item.side_effects is False
        assert item.cost_hint == "low"
        assert item.metadata == {}


class TestToolCardAlias:
    """Test that ToolCard is an alias for SelectableItem."""

    def test_alias_identity(self) -> None:
        assert ToolCard is SelectableItem

    def test_alias_instantiation(self) -> None:
        card = ToolCard(id="t", kind="tool", name="t", description="d")
        assert isinstance(card, SelectableItem)


class TestContextItem:
    """Tests for ContextItem serde."""

    def test_round_trip(self) -> None:
        item = ContextItem(
            id="ci1",
            kind=ItemKind.TOOL_RESULT,
            text="42 results found",
            token_estimate=8,
            metadata={"timestamp": 100.0},
            parent_id="tc1",
            artifact_ref="art_001",
        )
        d = item.to_dict()
        restored = ContextItem.from_dict(d)
        assert restored.id == "ci1"
        assert restored.kind is ItemKind.TOOL_RESULT
        assert restored.parent_id == "tc1"
        assert restored.artifact_ref == "art_001"
        assert restored.token_estimate == 8

    def test_kind_serialized_as_string(self) -> None:
        item = ContextItem(id="x", kind=ItemKind.USER_TURN, text="hi", token_estimate=1)
        d = item.to_dict()
        assert d["kind"] == "user_turn"

    def test_defaults(self) -> None:
        item = ContextItem(id="x", kind=ItemKind.USER_TURN, text="hi", token_estimate=1)
        assert item.metadata == {}
        assert item.parent_id is None
        assert item.artifact_ref is None


class TestArtifactRef:
    """Tests for ArtifactRef serde."""

    def test_round_trip(self) -> None:
        ref = ArtifactRef(handle="h1", media_type="application/json", size_bytes=1024, label="test")
        d = ref.to_dict()
        restored = ArtifactRef.from_dict(d)
        assert restored.handle == "h1"
        assert restored.size_bytes == 1024
        assert restored.label == "test"

    def test_optional_fields_default_to_none(self) -> None:
        d = {"handle": "h2", "media_type": "text/plain"}
        ref = ArtifactRef.from_dict(d)
        assert ref.size_bytes is None
        assert ref.label is None


class TestViewSpec:
    """Tests for ViewSpec serde."""

    def test_round_trip(self) -> None:
        vs = ViewSpec(view_id="v1", label="Head", selector={"type": "head"}, artifact_ref="art_1")
        d = vs.to_dict()
        restored = ViewSpec.from_dict(d)
        assert restored.view_id == "v1"
        assert restored.artifact_ref == "art_1"
        assert restored.selector == {"type": "head"}


class TestResultEnvelope:
    """Tests for ResultEnvelope serde."""

    def test_round_trip_with_nested(self) -> None:
        envelope = ResultEnvelope(
            status="ok",
            summary="Found 10 results",
            facts={"count": 10},
            artifacts=[ArtifactRef(handle="a1", media_type="text/plain", size_bytes=100)],
            views=[ViewSpec(view_id="v1", label="Head", artifact_ref="a1")],
            provenance={"tool": "search"},
        )
        d = envelope.to_dict()
        restored = ResultEnvelope.from_dict(d)
        assert restored.status == "ok"
        assert len(restored.artifacts) == 1
        assert restored.artifacts[0].handle == "a1"
        assert len(restored.views) == 1

    def test_empty_collections(self) -> None:
        envelope = ResultEnvelope(status="error", summary="fail")
        d = envelope.to_dict()
        assert d["artifacts"] == []
        restored = ResultEnvelope.from_dict(d)
        assert restored.artifacts == []
        assert restored.views == []


class TestBuildStats:
    """Tests for BuildStats serde."""

    def test_round_trip(self) -> None:
        stats = BuildStats(
            tokens_per_section={"facts": 10, "context_items": 50},
            total_candidates=20,
            included_count=15,
            dropped_count=5,
            dropped_reasons={"budget": 3, "lower_score": 2},
            dedup_removed=1,
            dependency_closures=2,
        )
        d = stats.to_dict()
        restored = BuildStats.from_dict(d)
        assert restored.total_candidates == 20
        assert restored.dedup_removed == 1
        assert restored.dropped_reasons == {"budget": 3, "lower_score": 2}
        assert restored.dependency_closures == 2

    def test_defaults(self) -> None:
        stats = BuildStats()
        assert stats.total_candidates == 0
        assert stats.tokens_per_section == {}
        assert stats.dedup_removed == 0

    def test_from_dict_empty(self) -> None:
        stats = BuildStats.from_dict({})
        assert stats.total_candidates == 0
        assert stats.dependency_closures == 0
        assert stats.dropped_reasons == {}

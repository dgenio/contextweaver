"""Tests for contextweaver.context.handoff (issue #294)."""

from __future__ import annotations

import json

from contextweaver.config import ContextPolicy
from contextweaver.context.handoff import (
    HANDOFF_CATEGORIES,
    HANDOFF_PACK_VERSION,
    HandoffEntry,
    SessionHandoffPack,
    build_session_handoff_pack,
    render_handoff_pack,
)
from contextweaver.protocols import CharDivFourEstimator
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.types import ArtifactRef, ContextItem, ItemKind, Sensitivity

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _item(
    iid: str,
    kind: ItemKind = ItemKind.plan_state,
    text: str = "",
    *,
    parent_id: str | None = None,
    sensitivity: Sensitivity = Sensitivity.public,
    metadata: dict[str, object] | None = None,
    artifact_ref: ArtifactRef | None = None,
) -> ContextItem:
    return ContextItem(
        id=iid,
        kind=kind,
        text=text or f"text for {iid}",
        sensitivity=sensitivity,
        metadata=dict(metadata or {}),
        parent_id=parent_id,
        artifact_ref=artifact_ref,
    )


def _seeded_log() -> tuple[InMemoryEventLog, InMemoryArtifactStore, ArtifactRef]:
    log = InMemoryEventLog()
    artifacts = InMemoryArtifactStore()
    ref = artifacts.put(
        handle="art:1",
        content=b"raw bytes",
        media_type="text/plain",
        label="raw output",
    )
    log.append(
        _item(
            "decide-1",
            kind=ItemKind.plan_state,
            text="Adopted MCP gateway over per-tool integration.",
            metadata={"handoff_category": "decision"},
        )
    )
    log.append(
        _item(
            "conv-1",
            kind=ItemKind.policy,
            text="All public APIs use snake_case.",
            metadata={"handoff_category": "convention"},
        )
    )
    log.append(
        _item(
            "todo-1",
            kind=ItemKind.user_turn,
            text="Investigate routing recall drop at 1000 tools.",
            metadata={"handoff_category": "unresolved"},
        )
    )
    log.append(
        _item(
            "pit-1",
            kind=ItemKind.tool_result,
            text="Tool X returns null on empty input — must guard.",
            metadata={"handoff_category": "pitfall"},
            artifact_ref=ref,
        )
    )
    log.append(
        _item(
            "next-1",
            kind=ItemKind.agent_msg,
            text="Read docs/architecture.md §4 before resuming.",
            metadata={"handoff_category": "next_step"},
        )
    )
    return log, artifacts, ref


# ---------------------------------------------------------------------------
# HandoffEntry / SessionHandoffPack dataclasses
# ---------------------------------------------------------------------------


def test_handoff_entry_round_trip_lossless() -> None:
    entry = HandoffEntry(
        id="e1",
        text="adopt protocol-based stores",
        category="decision",
        source_ids=["s1", "s2"],
        confidence=0.9,
        token_estimate=12,
    )
    assert HandoffEntry.from_dict(entry.to_dict()) == entry
    # JSON-serialisable.
    json.dumps(entry.to_dict())


def test_pack_round_trip_lossless() -> None:
    pack = SessionHandoffPack(
        decisions=[HandoffEntry(id="d1", text="d", category="decision")],
        conventions=[HandoffEntry(id="c1", text="c", category="convention")],
        unresolved_tasks=[HandoffEntry(id="u1", text="u", category="unresolved")],
        pitfalls=[HandoffEntry(id="p1", text="p", category="pitfall")],
        next_inspections=[HandoffEntry(id="n1", text="n", category="next_step")],
        artifact_refs=[ArtifactRef(handle="h", media_type="text/plain", size_bytes=5)],
        sensitivity_dropped=2,
        token_estimate=99,
    )
    rehydrated = SessionHandoffPack.from_dict(pack.to_dict())
    assert rehydrated == pack
    assert rehydrated.version == HANDOFF_PACK_VERSION


def test_pack_all_entries_walks_canonical_order() -> None:
    pack = SessionHandoffPack(
        decisions=[HandoffEntry(id="d", text="d", category="decision")],
        conventions=[HandoffEntry(id="c", text="c", category="convention")],
        unresolved_tasks=[HandoffEntry(id="u", text="u", category="unresolved")],
        pitfalls=[HandoffEntry(id="p", text="p", category="pitfall")],
        next_inspections=[HandoffEntry(id="n", text="n", category="next_step")],
    )
    assert [e.id for e in pack.all_entries()] == ["d", "c", "u", "p", "n"]


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


def test_build_classifies_explicit_categories() -> None:
    log, artifacts, _ = _seeded_log()
    pack = build_session_handoff_pack(
        log, artifacts, ContextPolicy(), CharDivFourEstimator(), budget_tokens=10_000
    )
    assert [e.id for e in pack.decisions] == ["decide-1"]
    assert [e.id for e in pack.conventions] == ["conv-1"]
    assert [e.id for e in pack.unresolved_tasks] == ["todo-1"]
    assert [e.id for e in pack.pitfalls] == ["pit-1"]
    assert [e.id for e in pack.next_inspections] == ["next-1"]
    assert all(e.confidence == 1.0 for e in pack.all_entries())


def test_build_heuristic_plan_state_classified_as_decision() -> None:
    log = InMemoryEventLog()
    log.append(_item("p1", kind=ItemKind.plan_state, text="picked beam search"))
    pack = build_session_handoff_pack(
        log, InMemoryArtifactStore(), ContextPolicy(), CharDivFourEstimator()
    )
    assert [e.id for e in pack.decisions] == ["p1"]
    # Heuristic confidence is lower than explicit.
    assert pack.decisions[0].confidence == 0.5


def test_build_heuristic_policy_classified_as_convention() -> None:
    log = InMemoryEventLog()
    log.append(_item("pol", kind=ItemKind.policy, text="<=300 lines per module"))
    pack = build_session_handoff_pack(
        log, InMemoryArtifactStore(), ContextPolicy(), CharDivFourEstimator()
    )
    assert [e.id for e in pack.conventions] == ["pol"]


def test_build_heuristic_tool_failure_classified_as_pitfall() -> None:
    log = InMemoryEventLog()
    log.append(
        _item(
            "tr",
            kind=ItemKind.tool_result,
            text="timeout after 30s",
            metadata={"status": "failed"},
        )
    )
    pack = build_session_handoff_pack(
        log, InMemoryArtifactStore(), ContextPolicy(), CharDivFourEstimator()
    )
    assert [e.id for e in pack.pitfalls] == ["tr"]


def test_build_firewalls_tool_result_text_before_rendering() -> None:
    log = InMemoryEventLog()
    raw_text = f"{'x' * 600} RAW_TAIL_SHOULD_NOT_RENDER"
    log.append(
        _item(
            "tr",
            kind=ItemKind.tool_result,
            text=raw_text,
            metadata={"status": "failed"},
        )
    )
    pack = build_session_handoff_pack(
        log, InMemoryArtifactStore(), ContextPolicy(), CharDivFourEstimator(), budget_tokens=1000
    )
    rendered = render_handoff_pack(pack)
    assert [e.id for e in pack.pitfalls] == ["tr"]
    assert "RAW_TAIL_SHOULD_NOT_RENDER" not in rendered
    assert pack.artifact_refs


def test_build_drops_sensitive_items_by_default() -> None:
    # Default policy: floor=confidential, action=drop.
    log = InMemoryEventLog()
    log.append(
        _item(
            "secret",
            kind=ItemKind.plan_state,
            text="API key: sk-12345",
            sensitivity=Sensitivity.restricted,
            metadata={"handoff_category": "decision"},
        )
    )
    log.append(
        _item(
            "ok",
            kind=ItemKind.plan_state,
            text="Use TF-IDF for routing",
            sensitivity=Sensitivity.public,
            metadata={"handoff_category": "decision"},
        )
    )
    pack = build_session_handoff_pack(
        log, InMemoryArtifactStore(), ContextPolicy(), CharDivFourEstimator()
    )
    decision_ids = [e.id for e in pack.decisions]
    assert "ok" in decision_ids
    assert "secret" not in decision_ids
    assert pack.sensitivity_dropped == 1
    # Raw text MUST NOT survive — invariant from sensitivity rules.
    rendered = render_handoff_pack(pack)
    assert "sk-12345" not in rendered
    assert "PASSWORD" not in rendered


def test_build_redact_mode_text_replaced_not_raw() -> None:
    log = InMemoryEventLog()
    log.append(
        _item(
            "secret",
            kind=ItemKind.plan_state,
            text="THE PASSWORD IS 42",
            sensitivity=Sensitivity.restricted,
            metadata={"handoff_category": "decision"},
        )
    )
    policy = ContextPolicy(sensitivity_action="redact", redaction_hooks=["mask"])
    pack = build_session_handoff_pack(log, InMemoryArtifactStore(), policy, CharDivFourEstimator())
    # Item kept (redact mode) but text is the mask placeholder.
    assert len(pack.decisions) == 1
    assert pack.decisions[0].text == "[REDACTED: restricted]"
    assert "PASSWORD" not in pack.decisions[0].text


def test_build_respects_token_budget() -> None:
    # Build a log of long entries; budget should cut off after a couple.
    log = InMemoryEventLog()
    long_text = "x" * 400  # ~100 tokens under CharDivFourEstimator
    for i in range(5):
        log.append(
            _item(
                f"d{i}",
                kind=ItemKind.plan_state,
                text=long_text,
                metadata={"handoff_category": "decision"},
            )
        )
    pack = build_session_handoff_pack(
        log, InMemoryArtifactStore(), ContextPolicy(), CharDivFourEstimator(), budget_tokens=150
    )
    # 100 tokens fits, 200 doesn't — so exactly one entry.
    assert len(pack.decisions) == 1
    assert pack.token_estimate <= 150


def test_build_includes_artifact_refs_from_dependency_chain() -> None:
    log, artifacts, ref = _seeded_log()
    # Add a child of pit-1 — its parent has the artifact_ref.
    log.append(
        _item(
            "child",
            kind=ItemKind.plan_state,
            text="follow-up on pitfall pit-1",
            parent_id="pit-1",
            metadata={"handoff_category": "decision"},
        )
    )
    pack = build_session_handoff_pack(
        log, artifacts, ContextPolicy(), CharDivFourEstimator(), budget_tokens=10_000
    )
    handles = [a.handle for a in pack.artifact_refs]
    assert ref.handle in handles or "artifact:pit-1" in handles


def test_build_does_not_cite_artifacts_from_sensitive_dropped_parent() -> None:
    log = InMemoryEventLog()
    artifacts = InMemoryArtifactStore()
    ref = artifacts.put(
        handle="secret-parent",
        content=b"raw secret",
        media_type="text/plain",
        label="restricted tool output",
    )
    log.append(
        _item(
            "parent",
            kind=ItemKind.tool_result,
            text="API key: sk-12345",
            sensitivity=Sensitivity.restricted,
            artifact_ref=ref,
        )
    )
    log.append(
        _item(
            "child",
            kind=ItemKind.plan_state,
            text="Use the public follow-up decision.",
            parent_id="parent",
            metadata={"handoff_category": "decision"},
        )
    )
    pack = build_session_handoff_pack(
        log, artifacts, ContextPolicy(), CharDivFourEstimator(), budget_tokens=10_000
    )
    assert [e.id for e in pack.decisions] == ["child"]
    assert pack.sensitivity_dropped == 1
    assert [a.handle for a in pack.artifact_refs] == []


def test_build_artifact_refs_deduplicated() -> None:
    # Two entries sharing the same artifact handle should not duplicate it.
    log = InMemoryEventLog()
    artifacts = InMemoryArtifactStore()
    ref = artifacts.put(handle="shared", content=b"x", media_type="text/plain")
    log.append(
        _item(
            "a",
            kind=ItemKind.plan_state,
            text="entry a",
            metadata={"handoff_category": "decision"},
            artifact_ref=ref,
        )
    )
    log.append(
        _item(
            "b",
            kind=ItemKind.plan_state,
            text="entry b",
            metadata={"handoff_category": "decision"},
            artifact_ref=ref,
        )
    )
    pack = build_session_handoff_pack(
        log, artifacts, ContextPolicy(), CharDivFourEstimator(), budget_tokens=10_000
    )
    handles = [a.handle for a in pack.artifact_refs]
    assert handles.count("shared") == 1


def test_build_unknown_category_value_ignored() -> None:
    # Random handoff_category strings must not crash; item is just skipped.
    log = InMemoryEventLog()
    log.append(
        _item(
            "x",
            kind=ItemKind.user_turn,
            text="random",
            metadata={"handoff_category": "nonsense"},
        )
    )
    pack = build_session_handoff_pack(
        log, InMemoryArtifactStore(), ContextPolicy(), CharDivFourEstimator()
    )
    assert pack.all_entries() == []


def test_build_deterministic_across_runs() -> None:
    # Same input → byte-identical to_dict() across two builds.
    log, artifacts, _ = _seeded_log()
    pack_a = build_session_handoff_pack(
        log, artifacts, ContextPolicy(), CharDivFourEstimator(), budget_tokens=10_000
    )
    pack_b = build_session_handoff_pack(
        log, artifacts, ContextPolicy(), CharDivFourEstimator(), budget_tokens=10_000
    )
    assert json.dumps(pack_a.to_dict(), sort_keys=True) == json.dumps(
        pack_b.to_dict(), sort_keys=True
    )


def test_build_explicit_beats_heuristic_in_sort() -> None:
    # An explicit-tagged entry and a heuristic-tagged entry of same category:
    # explicit (confidence=1.0) should land first.
    log = InMemoryEventLog()
    log.append(_item("heur", kind=ItemKind.plan_state, text="heuristic decision"))
    log.append(
        _item(
            "expl",
            kind=ItemKind.plan_state,
            text="explicit decision",
            metadata={"handoff_category": "decision"},
        )
    )
    pack = build_session_handoff_pack(
        log, InMemoryArtifactStore(), ContextPolicy(), CharDivFourEstimator()
    )
    # Both kept; explicit lands first due to descending-confidence sort.
    assert [e.id for e in pack.decisions] == ["expl", "heur"]


def test_categories_constant_matches_pack_fields() -> None:
    # Guard against drift between the canonical tuple and dataclass fields.
    pack = SessionHandoffPack()
    field_to_category = {
        "decisions": "decision",
        "conventions": "convention",
        "unresolved_tasks": "unresolved",
        "pitfalls": "pitfall",
        "next_inspections": "next_step",
    }
    for field_name, category in field_to_category.items():
        assert hasattr(pack, field_name)
        assert category in HANDOFF_CATEGORIES


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_render_omits_empty_buckets() -> None:
    pack = SessionHandoffPack(
        decisions=[HandoffEntry(id="d1", text="decision text", category="decision")]
    )
    rendered = render_handoff_pack(pack)
    assert "## Decisions" in rendered
    assert "## Conventions" not in rendered
    assert "## Pitfalls" not in rendered


def test_render_lists_artifacts_appendix() -> None:
    pack = SessionHandoffPack(
        decisions=[HandoffEntry(id="d1", text="text", category="decision")],
        artifact_refs=[
            ArtifactRef(handle="h:1", media_type="text/plain", size_bytes=42, label="logs")
        ],
    )
    rendered = render_handoff_pack(pack)
    assert "## Cited artefacts" in rendered
    assert "h:1" in rendered
    assert "logs" in rendered


def test_render_ends_with_newline() -> None:
    pack = SessionHandoffPack(decisions=[HandoffEntry(id="d1", text="text", category="decision")])
    assert render_handoff_pack(pack).endswith("\n")


def test_render_empty_pack_has_header_only() -> None:
    rendered = render_handoff_pack(SessionHandoffPack())
    assert rendered.strip() == f"# Session handoff (v{HANDOFF_PACK_VERSION})"


def test_render_uses_canonical_category_order() -> None:
    pack = SessionHandoffPack(
        decisions=[HandoffEntry(id="d", text="d", category="decision")],
        conventions=[HandoffEntry(id="c", text="c", category="convention")],
        unresolved_tasks=[HandoffEntry(id="u", text="u", category="unresolved")],
        pitfalls=[HandoffEntry(id="p", text="p", category="pitfall")],
        next_inspections=[HandoffEntry(id="n", text="n", category="next_step")],
    )
    rendered = render_handoff_pack(pack)
    # Order of section headings must match HANDOFF_CATEGORIES.
    positions = {
        "## Decisions": rendered.index("## Decisions"),
        "## Conventions": rendered.index("## Conventions"),
        "## Unresolved tasks": rendered.index("## Unresolved tasks"),
        "## Pitfalls": rendered.index("## Pitfalls"),
        "## Next inspection points": rendered.index("## Next inspection points"),
    }
    ordered = sorted(positions, key=positions.__getitem__)
    assert ordered == [
        "## Decisions",
        "## Conventions",
        "## Unresolved tasks",
        "## Pitfalls",
        "## Next inspection points",
    ]

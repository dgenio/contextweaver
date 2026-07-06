"""Tests for contextweaver.adapters.lessons (issue #767)."""

from __future__ import annotations

from pathlib import Path

from contextweaver.adapters.lessons import (
    LessonSelectionPolicy,
    eligible_lessons,
    lesson_nodes_to_context_items,
    load_lesson_bundle,
    select_lessons,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "lessons" / "mixed_lifecycle"

# expires_at in the fixture is 1_000_000_000; this reference time is well past it.
LATER_NOW = 1_500_000_000.0


def _load() -> list:
    nodes, _diagnostics = load_lesson_bundle(FIXTURE_DIR)
    return nodes


def test_load_lesson_bundle_returns_all_lifecycle_states() -> None:
    nodes = _load()
    ids = {n.id for n in nodes}
    assert ids == {
        "lesson-active",
        "lesson-candidate",
        "lesson-deprecated",
        "lesson-rejected",
        "lesson-expired",
        "lesson-wrong-scope",
    }


# ---------------------------------------------------------------------------
# eligible_lessons — the six explicit lifecycle states (issue #767)
# ---------------------------------------------------------------------------


def test_eligible_lessons_default_policy_excludes_rejected_and_deprecated() -> None:
    nodes = _load()
    eligible, excluded = eligible_lessons(nodes, LessonSelectionPolicy(), now=LATER_NOW)
    eligible_ids = {n.id for n in eligible}
    assert "lesson-rejected" not in eligible_ids
    assert "lesson-deprecated" not in eligible_ids
    reasons = {e.node_id: e.reason for e in excluded}
    assert reasons["lesson-rejected"] == "status:rejected"
    assert reasons["lesson-deprecated"] == "status:deprecated"


def test_eligible_lessons_default_policy_excludes_unreviewed_candidate() -> None:
    nodes = _load()
    eligible, excluded = eligible_lessons(nodes, LessonSelectionPolicy(), now=LATER_NOW)
    assert "lesson-candidate" not in {n.id for n in eligible}
    assert any(e.node_id == "lesson-candidate" for e in excluded)


def test_eligible_lessons_include_candidates_opts_in() -> None:
    nodes = _load()
    policy = LessonSelectionPolicy(include_candidates=True)
    eligible, _excluded = eligible_lessons(nodes, policy, now=LATER_NOW)
    assert "lesson-candidate" in {n.id for n in eligible}


def test_eligible_lessons_excludes_expired() -> None:
    nodes = _load()
    eligible, excluded = eligible_lessons(nodes, LessonSelectionPolicy(), now=LATER_NOW)
    assert "lesson-expired" not in {n.id for n in eligible}
    reasons = {e.node_id: e.reason for e in excluded}
    assert reasons["lesson-expired"] == "expired"


def test_eligible_lessons_not_expired_before_boundary() -> None:
    nodes = _load()
    eligible, _excluded = eligible_lessons(nodes, LessonSelectionPolicy(), now=1.0)
    assert "lesson-expired" in {n.id for n in eligible}


def test_eligible_lessons_active_and_wrong_scope_are_both_eligible() -> None:
    """Scope affects ranking, not eligibility (issue #767 design constraint)."""
    nodes = _load()
    eligible, _excluded = eligible_lessons(nodes, LessonSelectionPolicy(), now=LATER_NOW)
    eligible_ids = {n.id for n in eligible}
    assert "lesson-active" in eligible_ids
    assert "lesson-wrong-scope" in eligible_ids


# ---------------------------------------------------------------------------
# select_lessons — ranking + scope preference + budget packing
# ---------------------------------------------------------------------------


def test_select_lessons_preferred_scope_ranks_first() -> None:
    nodes = _load()
    policy = LessonSelectionPolicy(preferred_scope="project")
    items, _excluded = select_lessons(
        nodes, "lesson", budget_tokens=10_000, policy=policy, now=LATER_NOW
    )
    item_ids = [i.metadata["_contextweaver"]["knowledge_source"]["id"] for i in items]
    assert item_ids.index("lesson-active") < item_ids.index("lesson-wrong-scope")


def test_select_lessons_excluded_are_reported() -> None:
    nodes = _load()
    _items, excluded = select_lessons(nodes, "lesson", budget_tokens=10_000, now=LATER_NOW)
    excluded_ids = {e.node_id for e in excluded}
    assert excluded_ids == {
        "lesson-rejected",
        "lesson-deprecated",
        "lesson-candidate",
        "lesson-expired",
    }
    assert excluded[0].to_dict() == {"node_id": excluded[0].node_id, "reason": excluded[0].reason}


def test_select_lessons_zero_budget_returns_empty_but_still_reports_exclusions() -> None:
    nodes = _load()
    items, excluded = select_lessons(nodes, "lesson", budget_tokens=0, now=LATER_NOW)
    assert items == []
    assert len(excluded) == 4


def test_select_lessons_deterministic() -> None:
    nodes = _load()
    first, _e1 = select_lessons(nodes, "lesson", budget_tokens=10_000, now=LATER_NOW)
    second, _e2 = select_lessons(nodes, "lesson", budget_tokens=10_000, now=LATER_NOW)
    assert [i.id for i in first] == [i.id for i in second]


# ---------------------------------------------------------------------------
# lesson_nodes_to_context_items — provenance preserves lifecycle metadata
# ---------------------------------------------------------------------------


def test_lesson_nodes_to_context_items_preserves_provenance() -> None:
    nodes = _load()
    active = [n for n in nodes if n.id == "lesson-active"]
    items = lesson_nodes_to_context_items(active)
    provenance = items[0].metadata["_contextweaver"]["knowledge_source"]
    assert provenance["status"] == "active"
    assert provenance["scope"] == "project"
    assert provenance["confidence"] == 0.9


def test_load_lesson_bundle_non_utf8_file_does_not_raise(tmp_path: Path) -> None:
    (tmp_path / "bad_encoding.md").write_bytes(b"---\nid: bad-enc\nstatus: active\n---\n\xff\xfe")
    nodes, _diagnostics = load_lesson_bundle(tmp_path)
    assert len(nodes) == 1
    assert "�" in nodes[0].text

"""Tests for contextweaver.context.scoring."""

from __future__ import annotations

from contextweaver.config import ScoringConfig
from contextweaver.context.scoring import score_candidates, score_item
from contextweaver.types import ContextItem, ItemKind


def _item(iid: str, kind: ItemKind = ItemKind.user_turn, text: str = "hello") -> ContextItem:
    return ContextItem(id=iid, kind=kind, text=text, token_estimate=10)


def test_score_item_returns_float() -> None:
    item = _item("i1", text="search the database")
    cfg = ScoringConfig()
    score = score_item(item, "database search", 0, 1, [], cfg)
    assert isinstance(score, float)
    assert score >= 0.0


def test_score_candidates_sorted() -> None:
    items = [
        _item("i1", ItemKind.user_turn, "search database records"),
        _item("i2", ItemKind.doc_snippet, "completely unrelated content"),
        _item("i3", ItemKind.user_turn, "find data in database"),
    ]
    cfg = ScoringConfig()
    scored = score_candidates(items, "database search", [], cfg)
    assert len(scored) == 3
    # Scores should be in descending order
    scores = [s for s, _ in scored]
    assert scores == sorted(scores, reverse=True)


def test_score_candidates_deterministic() -> None:
    items = [_item(f"i{i}", text="same text here") for i in range(5)]
    cfg = ScoringConfig()
    r1 = score_candidates(items, "same text", [], cfg)
    r2 = score_candidates(items, "same text", [], cfg)
    assert [item.id for _, item in r1] == [item.id for _, item in r2]


def test_policy_kind_boost() -> None:
    policy_item = _item("p1", ItemKind.policy, "policy text")
    user_item = _item("u1", ItemKind.doc_snippet, "policy text")
    cfg = ScoringConfig()
    scored = score_candidates([policy_item, user_item], "policy", [], cfg)
    # policy kind should score higher
    assert scored[0][1].kind == ItemKind.policy


def test_kind_priority_override_changes_ordering() -> None:
    """A kind_priority override flips the default kind ranking (#487)."""
    doc = _item("d1", ItemKind.doc_snippet, "shared text")
    policy = _item("p1", ItemKind.policy, "shared text")
    # Default: policy (1.0) outranks doc_snippet (0.4).
    assert score_candidates([doc, policy], "shared", [], ScoringConfig())[0][1].id == "p1"
    # Override: lift doc_snippet above policy.
    boosted = ScoringConfig(kind_priority={ItemKind.doc_snippet: 1.0, ItemKind.policy: 0.1})
    assert score_candidates([doc, policy], "shared", [], boosted)[0][1].id == "d1"


def test_kind_priority_override_falls_back_for_unlisted_kinds() -> None:
    """Unlisted kinds keep the built-in priority (#487)."""
    cfg = ScoringConfig(kind_priority={ItemKind.doc_snippet: 0.9})
    # user_turn is not in the override, so its built-in 0.85 still applies and
    # outranks tool_result's built-in 0.55.
    user = _item("u1", ItemKind.user_turn, "x")
    tool = _item("t1", ItemKind.tool_result, "x")
    scored = score_candidates([tool, user], "y", [], cfg)
    assert scored[0][1].id == "u1"


def test_retrieved_doc_outranks_doc_snippet_by_default() -> None:
    """retrieved_doc carries a slightly higher default priority (#411)."""
    retrieved = _item("r1", ItemKind.retrieved_doc, "shared text")
    doc = _item("d1", ItemKind.doc_snippet, "shared text")
    scored = score_candidates([doc, retrieved], "shared", [], ScoringConfig())
    assert scored[0][1].id == "r1"

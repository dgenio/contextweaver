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

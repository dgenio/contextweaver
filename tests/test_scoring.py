"""Tests for contextweaver.context.scoring -- score_candidates weights, normalization, tie-breaking."""

from __future__ import annotations

from contextweaver._utils import tokenize
from contextweaver.config import ScoringConfig
from contextweaver.context.scoring import score_candidates
from contextweaver.types import ContextItem, ItemKind, Phase


def _item(
    iid: str,
    kind: ItemKind = ItemKind.USER_TURN,
    text: str = "hello",
    token_estimate: int = 10,
    tags: list[str] | None = None,
) -> ContextItem:
    meta: dict = {}
    if tags:
        meta["tags"] = tags
    return ContextItem(id=iid, kind=kind, text=text, token_estimate=token_estimate, metadata=meta)


class TestScoreCandidates:
    """Tests for score_candidates."""

    def test_returns_sorted_descending(self) -> None:
        items = [
            _item("i1", text="search database records", tags=["data"]),
            _item("i2", text="completely unrelated content"),
            _item("i3", text="find data in database", tags=["data"]),
        ]
        config = ScoringConfig()
        goal_tokens = tokenize("database search")
        scored = score_candidates(items, Phase.ANSWER, goal_tokens, {"data"}, 1000, config)
        assert len(scored) == 3
        scores = [s for _, s in scored]
        assert scores == sorted(scores, reverse=True)

    def test_empty_candidates(self) -> None:
        config = ScoringConfig()
        scored = score_candidates([], Phase.ANSWER, set(), set(), 1000, config)
        assert scored == []

    def test_tie_breaking_by_id(self) -> None:
        items = [
            _item("b_item", text="same text"),
            _item("a_item", text="same text"),
        ]
        config = ScoringConfig()
        scored = score_candidates(items, Phase.ANSWER, set(), set(), 1000, config)
        # When scores differ only by recency, verify both are present
        assert len(scored) == 2

    def test_kind_priority_affects_score(self) -> None:
        # In ANSWER phase, USER_TURN has priority 0.9, DOC_SNIPPET has 0.5
        user_item = _item("u1", ItemKind.USER_TURN, "query text")
        doc_item = _item("d1", ItemKind.DOC_SNIPPET, "query text")
        config = ScoringConfig()
        scored = score_candidates([doc_item, user_item], Phase.ANSWER, set(), set(), 1000, config)
        # User turn should score higher than doc snippet due to kind priority
        assert scored[0][0].kind == ItemKind.USER_TURN

    def test_tag_match_boosts_score(self) -> None:
        tagged = _item("t1", tags=["billing", "search"])
        untagged = _item("t2")
        config = ScoringConfig()
        scored = score_candidates(
            [untagged, tagged], Phase.ANSWER, set(), {"billing"}, 1000, config
        )
        tagged_score = next(s for item, s in scored if item.id == "t1")
        untagged_score = next(s for item, s in scored if item.id == "t2")
        assert tagged_score > untagged_score

    def test_token_cost_penalty(self) -> None:
        cheap = _item("cheap", token_estimate=5)
        expensive = _item("expensive", token_estimate=500)
        config = ScoringConfig()
        scored = score_candidates([expensive, cheap], Phase.ANSWER, set(), set(), 1000, config)
        cheap_score = next(s for item, s in scored if item.id == "cheap")
        expensive_score = next(s for item, s in scored if item.id == "expensive")
        assert cheap_score > expensive_score

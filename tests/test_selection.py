"""Tests for contextweaver.context.selection."""

from __future__ import annotations

from contextweaver.config import ContextBudget, ContextPolicy
from contextweaver.context.selection import select_and_pack
from contextweaver.protocols import CharDivFourEstimator
from contextweaver.types import ContextItem, ItemKind, Phase


def _item(iid: str, kind: ItemKind = ItemKind.user_turn, tokens: int = 100) -> ContextItem:
    return ContextItem(id=iid, kind=kind, text="x" * (tokens * 4), token_estimate=tokens)


def test_select_within_budget() -> None:
    budget = ContextBudget(answer=500)
    policy = ContextPolicy()
    estimator = CharDivFourEstimator()
    scored = [(1.0 - i * 0.1, _item(f"i{i}", tokens=100)) for i in range(3)]
    selected, stats = select_and_pack(scored, Phase.answer, budget, policy, estimator)
    total_tokens = sum(item.token_estimate for item in selected)
    assert total_tokens <= 500
    assert stats.included_count == len(selected)


def test_select_respects_kind_limit() -> None:
    budget = ContextBudget(answer=100000)
    policy = ContextPolicy()
    policy.max_items_per_kind[ItemKind.user_turn] = 2
    estimator = CharDivFourEstimator()
    scored = [(1.0, _item(f"i{i}", ItemKind.user_turn, tokens=10)) for i in range(5)]
    selected, stats = select_and_pack(scored, Phase.answer, budget, policy, estimator)
    user_turns = [s for s in selected if s.kind == ItemKind.user_turn]
    assert len(user_turns) == 2
    assert stats.dropped_reasons.get("kind_limit", 0) >= 3


def test_build_stats_populated() -> None:
    budget = ContextBudget(answer=1000)
    policy = ContextPolicy()
    estimator = CharDivFourEstimator()
    scored = [(1.0, _item("i1", tokens=100)), (0.5, _item("i2", tokens=100))]
    _, stats = select_and_pack(scored, Phase.answer, budget, policy, estimator)
    assert stats.total_candidates == 2
    assert stats.included_count + stats.dropped_count == 2

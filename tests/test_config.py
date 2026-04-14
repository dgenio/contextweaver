"""Tests for contextweaver.config."""

from __future__ import annotations

from contextweaver.config import ContextBudget, ContextPolicy, ScoringConfig
from contextweaver.types import ItemKind, Phase


def test_context_budget_defaults() -> None:
    b = ContextBudget()
    assert b.route == 2000
    assert b.answer == 6000


def test_context_budget_for_phase() -> None:
    b = ContextBudget(route=1000, call=2000, interpret=3000, answer=5000)
    assert b.for_phase(Phase.route) == 1000
    assert b.for_phase(Phase.call) == 2000
    assert b.for_phase(Phase.interpret) == 3000
    assert b.for_phase(Phase.answer) == 5000


def test_scoring_config_defaults() -> None:
    cfg = ScoringConfig()
    assert cfg.recency_weight == 0.3
    assert cfg.tag_match_weight == 0.25


def test_context_policy_defaults() -> None:
    policy = ContextPolicy()
    assert ItemKind.user_turn in policy.allowed_kinds_per_phase[Phase.route]


def test_context_policy_max_items_per_kind() -> None:
    policy = ContextPolicy()
    assert policy.max_items_per_kind[ItemKind.user_turn] == 50

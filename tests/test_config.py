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
    assert policy.ttl_behavior == "drop"


def test_context_policy_max_items_per_kind() -> None:
    policy = ContextPolicy()
    assert policy.max_items_per_kind[ItemKind.user_turn] == 50


# -- to_dict / from_dict round-trips ----------------------------------------


def test_scoring_config_roundtrip() -> None:
    cfg = ScoringConfig(recency_weight=0.5, token_cost_penalty=0.2)
    restored = ScoringConfig.from_dict(cfg.to_dict())
    assert restored.recency_weight == 0.5
    assert restored.token_cost_penalty == 0.2
    assert restored.tag_match_weight == 0.25  # default


def test_context_budget_roundtrip() -> None:
    b = ContextBudget(route=1500, answer=8000)
    restored = ContextBudget.from_dict(b.to_dict())
    assert restored.route == 1500
    assert restored.answer == 8000
    assert restored.call == 3000  # default


def test_context_policy_roundtrip() -> None:
    policy = ContextPolicy()
    d = policy.to_dict()
    restored = ContextPolicy.from_dict(d)
    assert restored.ttl_behavior == "drop"
    assert (
        ItemKind.user_turn
        in restored.allowed_kinds_per_phase[Phase.route]
    )
    assert restored.max_items_per_kind[ItemKind.user_turn] == 50

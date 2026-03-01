"""Tests for contextweaver.config -- budget for_phase, policy defaults, scoring config."""

from __future__ import annotations

from contextweaver.config import ContextBudget, ContextPolicy, ScoringConfig
from contextweaver.types import ItemKind, Phase, Sensitivity


class TestContextBudget:
    """Tests for ContextBudget defaults and for_phase."""

    def test_defaults(self) -> None:
        b = ContextBudget()
        assert b.route == 2000
        assert b.call == 3000
        assert b.interpret == 4000
        assert b.answer == 6000

    def test_for_phase_returns_correct_values(self) -> None:
        b = ContextBudget(route=1000, call=2000, interpret=3000, answer=5000)
        assert b.for_phase(Phase.ROUTE) == 1000
        assert b.for_phase(Phase.CALL) == 2000
        assert b.for_phase(Phase.INTERPRET) == 3000
        assert b.for_phase(Phase.ANSWER) == 5000

    def test_for_phase_all_phases(self) -> None:
        b = ContextBudget()
        for phase in Phase:
            result = b.for_phase(phase)
            assert isinstance(result, int)
            assert result > 0


class TestScoringConfig:
    """Tests for ScoringConfig defaults."""

    def test_defaults(self) -> None:
        cfg = ScoringConfig()
        assert cfg.recency_weight == 0.3
        assert cfg.tag_match_weight == 0.25
        assert cfg.kind_priority_weight == 0.35
        assert cfg.token_cost_penalty == 0.1

    def test_weights_sum_to_one(self) -> None:
        cfg = ScoringConfig()
        total = (
            cfg.recency_weight
            + cfg.tag_match_weight
            + cfg.kind_priority_weight
            + cfg.token_cost_penalty
        )
        assert abs(total - 1.0) < 1e-9


class TestContextPolicy:
    """Tests for ContextPolicy defaults and structure."""

    def test_default_allowed_kinds_route(self) -> None:
        policy = ContextPolicy()
        route_kinds = policy.allowed_kinds_per_phase[Phase.ROUTE]
        assert ItemKind.USER_TURN in route_kinds
        assert ItemKind.PLAN_STATE in route_kinds
        assert ItemKind.POLICY in route_kinds
        assert ItemKind.TOOL_RESULT not in route_kinds

    def test_default_allowed_kinds_answer_has_all(self) -> None:
        policy = ContextPolicy()
        answer_kinds = policy.allowed_kinds_per_phase[Phase.ANSWER]
        for kind in ItemKind:
            assert kind in answer_kinds

    def test_max_items_per_kind_default(self) -> None:
        policy = ContextPolicy()
        for kind in ItemKind:
            assert policy.max_items_per_kind[kind] == 50

    def test_ttl_behavior_default(self) -> None:
        policy = ContextPolicy()
        assert policy.ttl_behavior == "hard_drop"

    def test_sensitivity_floor_default(self) -> None:
        policy = ContextPolicy()
        assert policy.sensitivity_floor == Sensitivity.CONFIDENTIAL

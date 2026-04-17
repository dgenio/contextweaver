"""Tests for contextweaver.config."""

from __future__ import annotations

import pytest

from contextweaver.config import (
    ContextBudget,
    ContextPolicy,
    ProfileConfig,
    RoutingConfig,
    ScoringConfig,
)
from contextweaver.exceptions import ConfigError
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


# ---------------------------------------------------------------------------
# RoutingConfig
# ---------------------------------------------------------------------------


def test_routing_config_defaults() -> None:
    rc = RoutingConfig()
    assert rc.beam_width == 2
    assert rc.max_depth == 8
    assert rc.top_k == 10
    assert rc.confidence_gap == 0.15
    assert rc.max_children == 20


def test_routing_config_routing_kwargs_excludes_max_children() -> None:
    rc = RoutingConfig(beam_width=3, max_depth=6, top_k=15, confidence_gap=0.12, max_children=25)
    kwargs = rc.routing_kwargs()
    assert kwargs == {"beam_width": 3, "max_depth": 6, "top_k": 15, "confidence_gap": 0.12}
    assert "max_children" not in kwargs


def test_routing_config_roundtrip() -> None:
    rc = RoutingConfig(beam_width=4, max_depth=12, top_k=20, confidence_gap=0.10, max_children=30)
    assert RoutingConfig.from_dict(rc.to_dict()) == rc


def test_routing_config_from_dict_uses_defaults_for_missing_keys() -> None:
    assert RoutingConfig.from_dict({}) == RoutingConfig()


# ---------------------------------------------------------------------------
# ProfileConfig.from_preset — field values
# ---------------------------------------------------------------------------


def test_profile_fast_preset() -> None:
    p = ProfileConfig.from_preset("fast")
    assert p.routing.beam_width == 1
    assert p.routing.max_depth == 4
    assert p.routing.top_k == 5
    assert p.routing.confidence_gap == 0.20
    assert p.routing.max_children == 15
    assert p.budget.answer == 3000


def test_profile_balanced_preset() -> None:
    p = ProfileConfig.from_preset("balanced")
    assert p.routing.beam_width == 2
    assert p.routing.max_depth == 8
    assert p.routing.top_k == 10
    assert p.routing.confidence_gap == 0.15
    assert p.routing.max_children == 20
    assert p.budget.answer == 6000


def test_profile_accurate_preset() -> None:
    p = ProfileConfig.from_preset("accurate")
    assert p.routing.beam_width == 4
    assert p.routing.max_depth == 12
    assert p.routing.top_k == 20
    assert p.routing.confidence_gap == 0.10
    assert p.routing.max_children == 30
    assert p.budget.answer == 8000


def test_profile_unknown_preset_raises() -> None:
    with pytest.raises(ConfigError, match="Unknown preset"):
        ProfileConfig.from_preset("turbo")


# ---------------------------------------------------------------------------
# ProfileConfig — ordering invariants across presets
# ---------------------------------------------------------------------------


def test_preset_ordering_beam_width() -> None:
    fast = ProfileConfig.from_preset("fast")
    balanced = ProfileConfig.from_preset("balanced")
    accurate = ProfileConfig.from_preset("accurate")
    assert fast.routing.beam_width < balanced.routing.beam_width < accurate.routing.beam_width


def test_preset_ordering_max_depth() -> None:
    fast = ProfileConfig.from_preset("fast")
    balanced = ProfileConfig.from_preset("balanced")
    accurate = ProfileConfig.from_preset("accurate")
    assert fast.routing.max_depth < balanced.routing.max_depth < accurate.routing.max_depth


def test_preset_ordering_top_k() -> None:
    fast = ProfileConfig.from_preset("fast")
    balanced = ProfileConfig.from_preset("balanced")
    accurate = ProfileConfig.from_preset("accurate")
    assert fast.routing.top_k < balanced.routing.top_k < accurate.routing.top_k


def test_preset_ordering_budget_answer() -> None:
    fast = ProfileConfig.from_preset("fast")
    balanced = ProfileConfig.from_preset("balanced")
    accurate = ProfileConfig.from_preset("accurate")
    assert fast.budget.answer < balanced.budget.answer < accurate.budget.answer


def test_preset_ordering_confidence_gap() -> None:
    # accurate is more exploratory → lower confidence_gap threshold
    fast = ProfileConfig.from_preset("fast")
    balanced = ProfileConfig.from_preset("balanced")
    accurate = ProfileConfig.from_preset("accurate")
    assert (
        fast.routing.confidence_gap
        > balanced.routing.confidence_gap
        > accurate.routing.confidence_gap
    )


def test_preset_ordering_max_children() -> None:
    fast = ProfileConfig.from_preset("fast")
    balanced = ProfileConfig.from_preset("balanced")
    accurate = ProfileConfig.from_preset("accurate")
    assert fast.routing.max_children < balanced.routing.max_children < accurate.routing.max_children


# ---------------------------------------------------------------------------
# ProfileConfig — serialization roundtrip
# ---------------------------------------------------------------------------


def test_profile_config_roundtrip_from_preset() -> None:
    for name in ("fast", "balanced", "accurate"):
        original = ProfileConfig.from_preset(name)
        restored = ProfileConfig.from_dict(original.to_dict())
        assert restored.budget.answer == original.budget.answer
        assert restored.routing == original.routing
        assert restored.scoring.recency_weight == original.scoring.recency_weight


def test_profile_config_roundtrip_custom() -> None:
    p = ProfileConfig(
        budget=ContextBudget(route=500, call=1000, interpret=2000, answer=4000),
        scoring=ScoringConfig(recency_weight=0.5),
        routing=RoutingConfig(beam_width=3, top_k=7),
    )
    restored = ProfileConfig.from_dict(p.to_dict())
    assert restored.budget.answer == 4000
    assert restored.scoring.recency_weight == 0.5
    assert restored.routing.beam_width == 3
    assert restored.routing.top_k == 7


def test_profile_from_dict_empty_uses_defaults() -> None:
    p = ProfileConfig.from_dict({})
    assert p.budget.answer == 6000
    assert p.routing.beam_width == 2


# ---------------------------------------------------------------------------
# ProfileConfig.routing_kwargs() integration with Router
# ---------------------------------------------------------------------------


def test_profile_routing_kwargs_keys() -> None:
    p = ProfileConfig.from_preset("accurate")
    kwargs = p.routing.routing_kwargs()
    assert set(kwargs.keys()) == {"beam_width", "max_depth", "top_k", "confidence_gap"}

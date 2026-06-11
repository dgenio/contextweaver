"""Tests for contextweaver.config."""

from __future__ import annotations

import pytest

from contextweaver.config import ContextBudget, ContextPolicy, ScoringConfig
from contextweaver.exceptions import ConfigError
from contextweaver.profiles import Mode, ProfileConfig, RoutingConfig
from contextweaver.types import ItemKind, Phase, Sensitivity


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


# ---------------------------------------------------------------------------
# Mode enum (issue #45)
# ---------------------------------------------------------------------------


def test_mode_values() -> None:
    assert Mode.strict.value == "strict"
    assert Mode.seeded.value == "seeded"
    assert Mode.adaptive.value == "adaptive"


def test_mode_string_compatibility() -> None:
    """Mode is a str-Enum so str comparisons remain valid."""
    assert Mode.strict == "strict"
    assert Mode.strict.value == "strict"


def test_profile_default_mode_is_strict() -> None:
    assert ProfileConfig().mode == Mode.strict


# ---------------------------------------------------------------------------
# Mode.adaptive no-op warning (issue #521)
# ---------------------------------------------------------------------------


def test_profile_adaptive_mode_warns() -> None:
    """Selecting the inert Mode.adaptive must warn rather than silently no-op."""
    with pytest.warns(UserWarning, match="no effect"):
        ProfileConfig(mode=Mode.adaptive)


def test_profile_strict_and_seeded_do_not_warn() -> None:
    import warnings

    for mode in (Mode.strict, Mode.seeded):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            ProfileConfig(mode=mode)


def test_profile_adaptive_roundtrips_with_warning() -> None:
    """A persisted 'adaptive' profile round-trips but re-warns on load (issue #521)."""
    with pytest.warns(UserWarning):
        original = ProfileConfig(mode=Mode.adaptive)
    payload = original.to_dict()
    assert payload["mode"] == "adaptive"
    with pytest.warns(UserWarning, match="no effect"):
        restored = ProfileConfig.from_dict(payload)
    assert restored.mode is Mode.adaptive


def test_profile_explicit_mode() -> None:
    p = ProfileConfig(mode=Mode.seeded, seed=42)
    assert p.mode == Mode.seeded
    assert p.seed == 42


def test_profile_round_trip_preserves_mode() -> None:
    p = ProfileConfig(mode=Mode.seeded, seed=7)
    restored = ProfileConfig.from_dict(p.to_dict())
    assert restored.mode == Mode.seeded
    assert restored.seed == 7


def test_profile_round_trip_preserves_strict_default() -> None:
    p = ProfileConfig()
    restored = ProfileConfig.from_dict(p.to_dict())
    assert restored.mode == Mode.strict
    assert restored.seed is None


def test_profile_from_dict_unknown_mode_raises() -> None:
    with pytest.raises(ConfigError, match="Unknown mode"):
        ProfileConfig.from_dict({"mode": "wild"})


def test_profile_from_dict_missing_mode_defaults_strict() -> None:
    p = ProfileConfig.from_dict({})
    assert p.mode == Mode.strict


def test_from_profile_alias() -> None:
    """ProfileConfig.from_profile is an alias of from_preset."""
    a = ProfileConfig.from_profile("fast")
    b = ProfileConfig.from_preset("fast")
    assert a.routing.beam_width == b.routing.beam_width
    assert a.budget.answer == b.budget.answer


# ---------------------------------------------------------------------------
# ScoringConfig — to_dict / from_dict (#184) and dedup_threshold (#182)
# ---------------------------------------------------------------------------


def test_scoring_config_dedup_threshold_default() -> None:
    cfg = ScoringConfig()
    assert cfg.dedup_threshold == 0.85


def test_scoring_config_roundtrip() -> None:
    cfg = ScoringConfig(
        recency_weight=0.4,
        tag_match_weight=0.2,
        kind_priority_weight=0.3,
        token_cost_penalty=0.05,
        dedup_threshold=0.9,
    )
    restored = ScoringConfig.from_dict(cfg.to_dict())
    assert restored == cfg


def test_scoring_config_from_dict_defaults() -> None:
    assert ScoringConfig.from_dict({}) == ScoringConfig()


# ---------------------------------------------------------------------------
# ContextBudget — to_dict / from_dict (#184)
# ---------------------------------------------------------------------------


def test_context_budget_roundtrip() -> None:
    b = ContextBudget(route=1500, call=2500, interpret=3500, answer=5500)
    restored = ContextBudget.from_dict(b.to_dict())
    assert restored == b


def test_context_budget_from_dict_defaults() -> None:
    assert ContextBudget.from_dict({}) == ContextBudget()


# ---------------------------------------------------------------------------
# ContextPolicy — to_dict / from_dict (#184)
# ---------------------------------------------------------------------------


def test_context_policy_roundtrip() -> None:
    policy = ContextPolicy(
        sensitivity_floor=Sensitivity.restricted,
        sensitivity_action="redact",
        redaction_hooks=["mask"],
        extra={"custom_key": "custom_val"},
    )
    restored = ContextPolicy.from_dict(policy.to_dict())
    assert restored.sensitivity_floor == Sensitivity.restricted
    assert restored.sensitivity_action == "redact"
    assert restored.redaction_hooks == ["mask"]
    assert restored.extra == {"custom_key": "custom_val"}


def test_context_policy_roundtrip_allowed_kinds() -> None:
    policy = ContextPolicy()
    d = policy.to_dict()
    restored = ContextPolicy.from_dict(d)
    assert restored.allowed_kinds_per_phase.keys() == policy.allowed_kinds_per_phase.keys()
    for phase in Phase:
        assert restored.allowed_kinds_per_phase[phase] == policy.allowed_kinds_per_phase[phase]


def test_context_policy_roundtrip_max_items_per_kind() -> None:
    policy = ContextPolicy(max_items_per_kind={ItemKind.user_turn: 10, ItemKind.tool_call: 20})
    restored = ContextPolicy.from_dict(policy.to_dict())
    assert restored.max_items_per_kind[ItemKind.user_turn] == 10
    assert restored.max_items_per_kind[ItemKind.tool_call] == 20


def test_context_policy_from_dict_defaults() -> None:
    p = ContextPolicy.from_dict({})
    default = ContextPolicy()
    assert p.sensitivity_floor == default.sensitivity_floor
    assert p.sensitivity_action == default.sensitivity_action


# ---------------------------------------------------------------------------
# ProfileConfig — full roundtrip with policy (#184)
# ---------------------------------------------------------------------------


def test_profile_config_full_roundtrip_includes_policy() -> None:
    original = ProfileConfig(
        policy=ContextPolicy(
            sensitivity_floor=Sensitivity.restricted,
            extra={"test": True},
        ),
        scoring=ScoringConfig(dedup_threshold=0.92),
    )
    restored = ProfileConfig.from_dict(original.to_dict())
    assert restored.policy.sensitivity_floor == Sensitivity.restricted
    assert restored.policy.extra == {"test": True}
    assert restored.scoring.dedup_threshold == 0.92

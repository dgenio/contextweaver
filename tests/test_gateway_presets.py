"""Tests for contextweaver.adapters.gateway_presets (issue #664).

Named gateway policy presets bundling authz, retry, quota, and cache config.
"""

from __future__ import annotations

import pytest

from contextweaver.adapters.gateway_authz import PolicyContext
from contextweaver.adapters.gateway_presets import (
    GATEWAY_PRESET_NAMES,
    GATEWAY_PRESET_SCHEMA,
    CacheConfig,
    GatewayPreset,
)
from contextweaver.exceptions import ConfigError

# ---------------------------------------------------------------------------
# CacheConfig
# ---------------------------------------------------------------------------


def test_cache_config_default_is_disabled() -> None:
    cache = CacheConfig()
    assert cache.read_only is False
    assert cache.enabled is False
    assert cache.ttl_seconds == 60.0
    assert cache.max_entries == 256
    assert cache.allow is None


def test_cache_config_read_only_enables() -> None:
    assert CacheConfig(read_only=True).enabled is True


def test_cache_config_rejects_non_positive_ttl() -> None:
    with pytest.raises(ConfigError):
        CacheConfig(ttl_seconds=0)


def test_cache_config_rejects_non_positive_max_entries() -> None:
    with pytest.raises(ConfigError):
        CacheConfig(max_entries=0)


def test_cache_config_rejects_bare_string_allow() -> None:
    # A bare string is iterable, so it would otherwise collapse into a
    # per-character allow-list rather than a single tool_id.
    with pytest.raises(ConfigError):
        CacheConfig(allow="files:read")  # type: ignore[arg-type]


def test_cache_config_rejects_non_string_allow_entries() -> None:
    with pytest.raises(ConfigError):
        CacheConfig(allow=frozenset({"files:read", 1}))  # type: ignore[arg-type]


def test_cache_config_accepts_any_string_iterable_for_allow() -> None:
    assert CacheConfig(allow=["b:tool", "a:tool"]).to_dict()["allow"] == ["a:tool", "b:tool"]
    assert CacheConfig(allow=("a:tool",)).to_dict()["allow"] == ["a:tool"]
    assert CacheConfig(allow={"a:tool"}).to_dict()["allow"] == ["a:tool"]


def test_cache_config_coerces_allow_to_frozenset() -> None:
    # allow is annotated frozenset[str]; any accepted iterable is coerced so the
    # runtime value matches the annotation regardless of construction site.
    assert CacheConfig(allow=["a:tool", "b:tool"]).allow == frozenset({"a:tool", "b:tool"})
    for iterable in (["a:tool"], ("a:tool",), {"a:tool"}):
        assert isinstance(CacheConfig(allow=iterable).allow, frozenset)  # type: ignore[arg-type]


def test_cache_config_stays_hashable_with_list_allow() -> None:
    # A frozen dataclass must stay hashable; a non-coerced list-valued allow
    # would raise ``TypeError: unhashable type: 'list'`` here.
    assert hash(CacheConfig(allow=["a:tool"])) == hash(  # type: ignore[arg-type]
        CacheConfig(allow=frozenset({"a:tool"}))
    )


def test_cache_config_to_dict_sorts_allow() -> None:
    cache = CacheConfig(read_only=True, allow=frozenset({"b:tool", "a:tool"}))
    assert cache.to_dict()["allow"] == ["a:tool", "b:tool"]


def test_cache_config_to_dict_allow_none() -> None:
    assert CacheConfig().to_dict()["allow"] is None


# ---------------------------------------------------------------------------
# GatewayPreset.from_preset
# ---------------------------------------------------------------------------


def test_gateway_preset_names_are_safe_balanced_throughput() -> None:
    assert GATEWAY_PRESET_NAMES == ("safe", "balanced", "throughput")


def test_from_preset_unknown_name_raises_config_error() -> None:
    with pytest.raises(ConfigError, match="Unknown gateway policy preset"):
        GatewayPreset.from_preset("nope")


def test_from_preset_unknown_name_lists_valid_sorted() -> None:
    with pytest.raises(ConfigError, match='"balanced", "safe", "throughput"'):
        GatewayPreset.from_preset("nope")


def test_from_preset_builds_fresh_unshared_objects() -> None:
    first = GatewayPreset.from_preset("balanced")
    second = GatewayPreset.from_preset("balanced")
    assert first.policy is not second.policy
    assert first.rate_limits is not second.rate_limits
    assert first.retry == second.retry
    assert first == second


def test_from_preset_stamps_name_and_schema() -> None:
    preset = GatewayPreset.from_preset("safe")
    assert preset.name == "safe"
    assert preset.schema == GATEWAY_PRESET_SCHEMA


def test_safe_preset_requires_approval_on_execute_only() -> None:
    preset = GatewayPreset.from_preset("safe")
    assert preset.policy.decide(PolicyContext(meta_tool="tool_execute")).action == (
        "require_approval"
    )
    # Reads and tool_view egress are untouched by the 'safe' preset.
    assert preset.policy.decide(PolicyContext(meta_tool="tool_browse")).action == "allow"
    assert preset.policy.decide(PolicyContext(meta_tool="tool_view")).action == "allow"
    # Does not depend on the unverified read_only hint: a call declaring
    # read_only=True still requires approval.
    assert (
        preset.policy.decide(PolicyContext(meta_tool="tool_execute", read_only=True)).action
        == "require_approval"
    )


def test_safe_preset_retry_and_quota() -> None:
    preset = GatewayPreset.from_preset("safe")
    assert preset.retry.max_attempts == 2
    assert preset.rate_limits.per_meta_tool["tool_execute"].max_calls_per_minute == 30
    assert preset.cache.enabled is False


def test_balanced_preset_allows_everything() -> None:
    preset = GatewayPreset.from_preset("balanced")
    assert preset.policy.decide(PolicyContext(meta_tool="tool_execute")).action == "allow"
    assert preset.policy.rules == []
    assert preset.retry.max_attempts == 3
    assert preset.rate_limits.per_meta_tool["tool_execute"].max_calls_per_minute == 120
    assert preset.cache.enabled is False


def test_throughput_preset_allows_everything_and_caches() -> None:
    preset = GatewayPreset.from_preset("throughput")
    assert preset.policy.decide(PolicyContext(meta_tool="tool_execute")).action == "allow"
    assert preset.retry.max_attempts == 5
    assert preset.retry.jitter == 0.2
    assert preset.rate_limits.enabled is False
    assert preset.cache.enabled is True
    assert preset.cache.read_only is True


# ---------------------------------------------------------------------------
# GatewayPreset.to_dict — deterministic export
# ---------------------------------------------------------------------------


def test_to_dict_is_deterministic_across_calls() -> None:
    first = GatewayPreset.from_preset("throughput").to_dict()
    second = GatewayPreset.from_preset("throughput").to_dict()
    assert first == second


def test_to_dict_includes_schema_and_nested_blocks() -> None:
    payload = GatewayPreset.from_preset("safe").to_dict()
    assert payload["schema"] == GATEWAY_PRESET_SCHEMA
    assert payload["name"] == "safe"
    assert set(payload) == {"schema", "name", "policy", "retry", "rate_limits", "cache"}
    assert payload["policy"] == {
        "default": "allow",
        "rules": [
            {
                "action": "require_approval",
                "meta_tool": "tool_execute",
                "reason": "preset 'safe' requires approval for every tool_execute call",
            }
        ],
    }


@pytest.mark.parametrize("name", GATEWAY_PRESET_NAMES)
def test_to_dict_round_trips_nested_config(name: str) -> None:
    from contextweaver.adapters.gateway_authz import ToolPolicy
    from contextweaver.adapters.gateway_policy import RateLimitPolicy, RetryPolicy

    preset = GatewayPreset.from_preset(name)
    payload = preset.to_dict()
    assert ToolPolicy.from_dict(payload["policy"]).to_dict() == payload["policy"]
    assert RetryPolicy.from_dict(payload["retry"]).to_dict() == payload["retry"]
    # rate_limits/cache have no from_dict-round-trip via to_dict for the
    # per_tool-less shape used by presets today; verify field-level equality.
    rate_limits = RateLimitPolicy.from_dict(payload["rate_limits"])
    assert rate_limits.per_meta_tool.keys() == preset.rate_limits.per_meta_tool.keys()

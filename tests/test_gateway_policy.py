"""Tests for contextweaver.adapters.gateway_policy.

Pure-data config and result types for the gateway dispatch-path controls
(issues #529 / #482 / #483).
"""

from __future__ import annotations

import pytest

from contextweaver.adapters.gateway_policy import (
    DEFAULT_RETRYABLE_CODES,
    DryRunReport,
    RateLimit,
    RateLimitPolicy,
    RetryPolicy,
)
from contextweaver.exceptions import ConfigError

# ---------------------------------------------------------------------------
# RetryPolicy (#529)
# ---------------------------------------------------------------------------


def test_retry_policy_default_is_single_attempt() -> None:
    policy = RetryPolicy()
    assert policy.max_attempts == 1
    assert policy.enabled is False
    assert policy.retryable_codes == DEFAULT_RETRYABLE_CODES


def test_retry_policy_backoff_doubles_and_caps() -> None:
    policy = RetryPolicy(max_attempts=6, base_delay=1.0, max_delay=10.0)
    assert policy.backoff_delay(0) == 1.0
    assert policy.backoff_delay(1) == 2.0
    assert policy.backoff_delay(2) == 4.0
    assert policy.backoff_delay(3) == 8.0
    # 16.0 would exceed max_delay → capped.
    assert policy.backoff_delay(4) == 10.0


def test_retry_policy_jitter_applies_fraction_deterministically() -> None:
    policy = RetryPolicy(max_attempts=2, base_delay=4.0, max_delay=100.0, jitter=0.5)
    # full jitter fraction subtracts jitter*delay: 4 * (1 - 0.5*1.0) == 2.0
    assert policy.backoff_delay(0, jitter_fraction=1.0) == 2.0
    # zero jitter fraction leaves the delay untouched
    assert policy.backoff_delay(0, jitter_fraction=0.0) == 4.0


def test_retry_policy_round_trips_through_dict() -> None:
    policy = RetryPolicy(max_attempts=4, base_delay=0.25, max_delay=8.0, jitter=0.1)
    assert RetryPolicy.from_dict(policy.to_dict()) == policy


def test_retry_policy_from_dict_defaults_retryable_codes() -> None:
    policy = RetryPolicy.from_dict({"max_attempts": 3})
    assert policy.max_attempts == 3
    assert policy.retryable_codes == DEFAULT_RETRYABLE_CODES


def test_retry_policy_from_dict_accepts_explicit_codes() -> None:
    policy = RetryPolicy.from_dict({"retryable_codes": ["UPSTREAM_TIMEOUT"]})
    assert policy.retryable_codes == ("UPSTREAM_TIMEOUT",)


@pytest.mark.parametrize(
    "codes",
    [
        "UPSTREAM_TIMEOUT",  # bare string would split into characters
        ["UPSTREAM_TIMEOUT", 5],  # non-string member
        123,  # not a sequence
    ],
)
def test_retry_policy_from_dict_rejects_bad_retryable_codes(codes: object) -> None:
    with pytest.raises(ConfigError):
        RetryPolicy.from_dict({"retryable_codes": codes})


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": 0},
        {"base_delay": -1.0},
        {"jitter": 1.5},
    ],
)
def test_retry_policy_rejects_invalid_config(kwargs: dict[str, float]) -> None:
    with pytest.raises(ConfigError):
        RetryPolicy(**kwargs)


# ---------------------------------------------------------------------------
# RateLimit / RateLimitPolicy (#482)
# ---------------------------------------------------------------------------


def test_rate_limit_from_dict_parses_both_axes() -> None:
    limit = RateLimit.from_dict({"max_calls_per_minute": 30, "max_calls_per_session": 200})
    assert limit == RateLimit(max_calls_per_minute=30, max_calls_per_session=200)


def test_rate_limit_rejects_zero() -> None:
    with pytest.raises(ConfigError):
        RateLimit(max_calls_per_minute=0)


def test_rate_limit_policy_from_dict_matches_documented_shape() -> None:
    policy = RateLimitPolicy.from_dict(
        {
            "tool_execute": {"max_calls_per_minute": 30, "max_calls_per_session": 200},
            "per_tool": {"billing:refund": {"max_calls_per_session": 3}},
        }
    )
    assert policy.enabled is True
    assert policy.per_meta_tool["tool_execute"].max_calls_per_minute == 30
    assert policy.per_tool["billing:refund"].max_calls_per_session == 3


def test_rate_limit_policy_rejects_unknown_meta_tool() -> None:
    with pytest.raises(ConfigError):
        RateLimitPolicy.from_dict({"tool_nope": {"max_calls_per_minute": 1}})


def test_rate_limit_policy_empty_is_disabled() -> None:
    assert RateLimitPolicy().enabled is False


# ---------------------------------------------------------------------------
# DryRunReport (#483)
# ---------------------------------------------------------------------------


def test_dry_run_report_to_dict_shape() -> None:
    report = DryRunReport(
        tool_id="billing:refund@1#a1b2c3d4",
        upstream_name="refund",
        args_valid=True,
        annotations={"destructiveHint": True, "verified": False},
        checks=[{"name": "schema_validation", "status": "pass"}],
    )
    out = report.to_dict()
    assert out["dry_run"] is True
    assert out["tool_id"] == "billing:refund@1#a1b2c3d4"
    assert out["upstream_name"] == "refund"
    assert out["args_valid"] is True
    assert out["annotations"]["verified"] is False
    assert out["checks"] == [{"name": "schema_validation", "status": "pass"}]

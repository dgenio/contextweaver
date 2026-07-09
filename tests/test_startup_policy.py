"""Tests for contextweaver.adapters.startup_policy (issue #374)."""

from __future__ import annotations

import pytest

from contextweaver.adapters.startup_policy import (
    StartupPolicy,
    StartupReport,
    UpstreamStatus,
    detect_tool_name_collisions,
)
from contextweaver.exceptions import ConfigError

# ---------------------------------------------------------------------------
# StartupPolicy
# ---------------------------------------------------------------------------


def test_default_policy_is_degraded_with_one_required_healthy() -> None:
    policy = StartupPolicy()
    assert policy.mode == "degraded"
    assert policy.min_healthy_upstreams == 1
    assert policy.fail_on_empty_catalog is True


def test_invalid_mode_rejected() -> None:
    with pytest.raises(ConfigError, match="mode must be one of"):
        StartupPolicy(mode="chaotic")


def test_non_positive_upstream_timeout_rejected() -> None:
    with pytest.raises(ConfigError, match="must be positive"):
        StartupPolicy(upstream_timeout_seconds=0)


def test_negative_min_healthy_rejected() -> None:
    with pytest.raises(ConfigError, match="must be >= 0"):
        StartupPolicy(min_healthy_upstreams=-1)


def test_from_dict_rejects_unknown_key() -> None:
    with pytest.raises(ConfigError, match="unknown key"):
        StartupPolicy.from_dict({"mode": "strict", "bogus": True})


def test_from_dict_round_trip() -> None:
    policy = StartupPolicy.from_dict(
        {"mode": "strict", "min_healthy_upstreams": 2, "fail_on_empty_catalog": "false"}
    )
    assert policy.mode == "strict"
    assert policy.min_healthy_upstreams == 2
    assert policy.fail_on_empty_catalog is False


# ---------------------------------------------------------------------------
# StartupReport
# ---------------------------------------------------------------------------


def test_report_healthy_count_and_total_tools() -> None:
    report = StartupReport(
        statuses=(
            UpstreamStatus(name="a", status="loaded", tool_count=3),
            UpstreamStatus(name="b", status="failed", error="boom"),
            UpstreamStatus(name="c", status="loaded", tool_count=2),
        )
    )
    assert report.healthy_count == 2
    assert report.total_tools == 5


def test_report_render_lines_includes_collisions() -> None:
    report = StartupReport(
        statuses=(UpstreamStatus(name="a", status="loaded", tool_count=1),),
        collisions=("tool 'x' claimed by upstreams ['a', 'b']; 'a' wins (first-registered)",),
    )
    lines = report.render_lines()
    assert any("tools=1" in line for line in lines)
    assert any(line.startswith("collision:") for line in lines)


# ---------------------------------------------------------------------------
# detect_tool_name_collisions
# ---------------------------------------------------------------------------


def test_detect_collisions_none_when_disjoint() -> None:
    assert detect_tool_name_collisions({"a": ["x"], "b": ["y"]}) == []


def test_detect_collisions_reports_shared_names_in_declaration_order() -> None:
    # Declaration order ("b" then "a") must drive the reported winner, since
    # that is the order MultiplexUpstream actually resolves collisions in —
    # not alphabetical order.
    collisions = detect_tool_name_collisions({"b": ["shared"], "a": ["shared"]})
    assert len(collisions) == 1
    assert "'shared'" in collisions[0]
    assert "'b' wins" in collisions[0]

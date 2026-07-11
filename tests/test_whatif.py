"""Tests for contextweaver.eval.whatif."""

from __future__ import annotations

import pytest

from contextweaver.adapters.gateway_policy import RateLimit, RateLimitPolicy
from contextweaver.eval.whatif import ChurnScenario, WhatIfReport, simulate
from contextweaver.exceptions import ConfigError
from contextweaver.types import SelectableItem


def _item(iid: str, name: str, description: str, tags: list[str]) -> SelectableItem:
    return SelectableItem(id=iid, kind="tool", name=name, description=description, tags=tags)


def _catalog() -> list[SelectableItem]:
    return [
        _item("db_read", "read_db", "Read from database", ["data", "read"]),
        _item("db_write", "write_db", "Write to database", ["data", "write"]),
        _item("send_email", "send_email", "Send email notification", ["comm", "email"]),
        _item("search_docs", "search_docs", "Search documentation pages", ["search", "docs"]),
        _item("create_user", "create_user", "Create a new user account", ["admin", "users"]),
    ]


def _probes() -> list[tuple[str, str]]:
    return [
        ("read from database", "db_read"),
        ("send email notification", "send_email"),
        ("search documentation pages", "search_docs"),
        ("create a new user account", "create_user"),
    ]


def test_deterministic_across_runs_with_same_seed() -> None:
    scenario = ChurnScenario(name="mixed", add_tools=6, remove_tools=1, rename_tools=1)
    report_a = simulate(_catalog(), scenario, _probes(), seed=7)
    report_b = simulate(_catalog(), scenario, _probes(), seed=7)
    assert report_a.to_dict() == report_b.to_dict()


def test_removing_expected_tools_drops_recall() -> None:
    baseline = simulate(_catalog(), ChurnScenario(name="noop"), _probes(), seed=0)
    churned = simulate(_catalog(), ChurnScenario(name="rm", remove_tools=4), _probes(), seed=0)
    assert baseline.routing_recall_before == baseline.routing_recall_after
    assert churned.routing_recall_after < churned.routing_recall_before
    assert churned.catalog_size_after == 1
    assert any(note.startswith("removed:") for note in churned.notes)


def test_distractor_only_churn_keeps_recall() -> None:
    scenario = ChurnScenario(name="distractors", add_tools=10)
    report = simulate(_catalog(), scenario, _probes(), seed=3)
    assert report.catalog_size_after == report.catalog_size_before + 10
    # Generic synthetic tools must not displace the expected ids from top-5.
    assert report.routing_recall_after >= report.routing_recall_before


def test_renaming_expected_tools_counts_as_miss() -> None:
    scenario = ChurnScenario(name="rename-all", rename_tools=5)
    report = simulate(_catalog(), scenario, _probes(), seed=0)
    assert report.routing_recall_after == 0.0
    assert report.shortlist_stability == 0.0
    assert any(note.startswith("renamed:") for note in report.notes)


def test_rate_limit_breaches_counted_with_tight_policy() -> None:
    policy = RateLimitPolicy(
        per_meta_tool={"tool_execute": RateLimit(max_calls_per_minute=5)},
    )
    scenario = ChurnScenario(name="spike", traffic_multiplier=2.0, duration_ticks=3)
    report = simulate(_catalog(), scenario, _probes(), rate_limit=policy, requests_per_tick=10)
    # 3 ticks x 20 requests, only 5 fit in the sliding minute window.
    assert report.rate_limit_breaches == 55


def test_no_rate_limit_policy_means_zero_breaches() -> None:
    scenario = ChurnScenario(name="spike", traffic_multiplier=10.0, duration_ticks=2)
    report = simulate(_catalog(), scenario, _probes())
    assert report.rate_limit_breaches == 0


def test_markdown_renders() -> None:
    report = simulate(_catalog(), ChurnScenario(name="md", add_tools=2), _probes())
    text = report.render_markdown()
    assert "# What-If Report: md" in text
    assert "Routing recall@5" in text
    assert "Rate-limit breaches: 0" in text
    # Deterministic: identical render on a second call.
    assert text == report.render_markdown()


def test_scenario_serde_round_trip() -> None:
    scenario = ChurnScenario(
        name="serde",
        add_tools=3,
        remove_tools=2,
        rename_tools=1,
        traffic_multiplier=1.5,
        duration_ticks=30,
    )
    assert ChurnScenario.from_dict(scenario.to_dict()) == scenario


def test_report_serde_round_trip() -> None:
    report = simulate(
        _catalog(), ChurnScenario(name="serde", add_tools=1, remove_tools=1), _probes(), seed=5
    )
    assert WhatIfReport.from_dict(report.to_dict()) == report


def test_invalid_inputs_raise_config_error() -> None:
    with pytest.raises(ConfigError):
        ChurnScenario(name="bad", add_tools=-1)
    with pytest.raises(ConfigError):
        ChurnScenario(name="bad", duration_ticks=0)
    with pytest.raises(ConfigError):
        simulate([], ChurnScenario(name="x"), _probes())
    with pytest.raises(ConfigError):
        simulate(_catalog(), ChurnScenario(name="x"), [])

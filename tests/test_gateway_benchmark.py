"""Tests for the gateway-scenario benchmark suite (issue #270)."""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks import gateway_benchmark as gb


def test_build_report_emits_four_scenarios() -> None:
    """The default scenario set covers tiny / medium / large / huge payloads."""
    report = gb._build_report()
    names = {s["name"] for s in report["scenarios"]}
    assert names == {
        "tiny_no_firewall",
        "medium_crm",
        "bigquery_rowset",
        "slack_thread_review",
    }


def test_report_summary_distinguishes_firewall_trigger() -> None:
    """One scenario stays below the threshold; three exceed it."""
    report = gb._build_report()
    summary = report["summary"]
    assert summary["scenario_count"] == 4
    assert summary["firewall_no_op_count"] == 1
    assert summary["firewall_triggered_count"] == 3


def test_firewall_reduction_is_a_range_not_a_single_number() -> None:
    """The whole point of #270: the marketing 98.8 % becomes a measured range.

    The min and max reduction percentages must both be reported and must
    differ (otherwise the scorecard is back to a single anecdote)."""
    report = gb._build_report()
    summary = report["summary"]
    lo = summary["firewall_reduction_min_pct"]
    hi = summary["firewall_reduction_max_pct"]
    assert lo > 0.0
    assert hi > lo, (
        "min and max firewall reductions collapsed to a single number — "
        "the suite must surface a real range"
    )
    # Every triggered scenario should achieve high reduction; pin a soft
    # lower bound so a regression that bypasses the firewall flips this.
    assert lo > 90.0


def test_tiny_scenario_correctly_no_ops_the_firewall() -> None:
    """The ``tiny_no_firewall`` scenario must NOT trigger compaction —
    matches the issue body's explicit ask that the firewall correctly
    declines on small inputs."""
    report = gb._build_report()
    tiny = next(s for s in report["scenarios"] if s["name"] == "tiny_no_firewall")
    assert tiny["firewall_triggered"] is False
    assert tiny["firewall_reduction_pct"] == 0.0
    assert tiny["injected_summary_chars"] == tiny["raw_result_chars"]


def test_bigquery_scenario_routes_to_intended_tool() -> None:
    """The marquee scenario must still route to ``bigquery.run_query``."""
    report = gb._build_report()
    bq = next(s for s in report["scenarios"] if s["name"] == "bigquery_rowset")
    assert bq["selected_tool_id"] == "bigquery.run_query"
    assert bq["chosen_was_intent"] is True


def test_every_scenario_routes_to_a_real_catalog_tool() -> None:
    """No scenario should fall back to an empty shortlist."""
    report = gb._build_report()
    for scenario in report["scenarios"]:
        assert scenario["selected_tool_id"], f"empty selection in {scenario['name']}"
        assert scenario["exposed_choice_cards"] >= 1


def test_check_mode_passes_on_freshly_written_baseline(tmp_path: Path) -> None:
    """``--check`` is byte-stable when run twice in a row."""
    out_path = tmp_path / "gateway.json"
    rc = gb.main(["--output", str(out_path)])
    assert rc == 0
    assert out_path.exists()
    rc_check = gb.main(["--output", str(out_path), "--check"])
    assert rc_check == 0


def test_check_mode_fails_when_baseline_drifts(tmp_path: Path) -> None:
    """``--check`` exits 1 when the on-disk file disagrees with the run."""
    out_path = tmp_path / "gateway.json"
    rc = gb.main(["--output", str(out_path)])
    assert rc == 0
    baseline = json.loads(out_path.read_text(encoding="utf-8"))
    # Drift the baseline so re-running with --check trips.
    baseline["summary"]["firewall_reduction_mean_pct"] = 0.0
    out_path.write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    rc_check = gb.main(["--output", str(out_path), "--check"])
    assert rc_check == 1


def test_check_mode_fails_on_missing_baseline(tmp_path: Path) -> None:
    missing = tmp_path / "absent.json"
    rc = gb.main(["--output", str(missing), "--check"])
    assert rc == 1

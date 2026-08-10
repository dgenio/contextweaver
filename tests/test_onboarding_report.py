"""Tests for the privacy-safe onboarding/adoption evidence reporter."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import onboarding_report  # noqa: E402

ROOT = Path(__file__).parent.parent
SCHEMA = ROOT / "benchmarks" / "onboarding" / "schema.json"
FIXTURES = ROOT / "benchmarks" / "onboarding" / "fixtures"


def _record(run_id: str = "run-1") -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "participant_id": "PRIVATE-PARTICIPANT",
        "synthetic": False,
        "cohort": "unassisted_evaluator",
        "scenario_id": "real-source-first-success",
        "contextweaver_version": "0.18.1",
        "started_at": None,
        "first_success": True,
        "seconds_to_first_success": 75.0,
        "meaningful_receipt": "compiled_bundle",
        "real_source": True,
        "maintainer_interventions": 0,
        "failures": ["docs_mismatch"],
        "rollback_seconds": 20.0,
        "retention": {
            "day7": "not_due",
            "day30": "not_due",
            "removed_reason": None,
        },
        "notes": "PRIVATE NOTES MUST NOT APPEAR IN REPORT",
        "consent_public": False,
    }


def _write_record(directory: Path, payload: dict[str, object], filename: str = "run.json") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(json.dumps(payload), encoding="utf-8")


def test_synthetic_fixture_validates() -> None:
    runs = onboarding_report.load_runs(FIXTURES, SCHEMA)
    assert len(runs) == 1
    assert runs[0]["synthetic"] is True


def test_synthetic_is_excluded_from_default_summary() -> None:
    runs = onboarding_report.load_runs(FIXTURES, SCHEMA)
    summary = onboarding_report.summarize(runs)
    assert summary["included_runs"] == 0
    assert summary["unassisted"]["runs"] == 0


def test_synthetic_can_be_included_for_smoke_reporting() -> None:
    runs = onboarding_report.load_runs(FIXTURES, SCHEMA)
    summary = onboarding_report.summarize(runs, include_synthetic=True)
    assert summary["included_runs"] == 1
    assert summary["unassisted"]["success_rate_pct"] == 100.0
    assert summary["unassisted"]["median_seconds_to_first_success"] == 52.0


def test_real_summary_tracks_unassisted_friction_without_identity(tmp_path: Path) -> None:
    _write_record(tmp_path, _record())
    runs = onboarding_report.load_runs(tmp_path, SCHEMA)
    summary = onboarding_report.summarize(runs)
    report = onboarding_report.render_markdown(summary)

    assert summary["unassisted"]["real_source_success_rate_pct"] == 100.0
    assert summary["failure_counts"] == {"docs_mismatch": 1}
    assert "PRIVATE-PARTICIPANT" not in report
    assert "PRIVATE NOTES" not in report


def test_duplicate_run_ids_fail_closed(tmp_path: Path) -> None:
    _write_record(tmp_path, _record("same"), "a.json")
    _write_record(tmp_path, _record("same"), "b.json")
    with pytest.raises(ValueError, match="duplicate onboarding run_id"):
        onboarding_report.load_runs(tmp_path, SCHEMA)


def test_success_requires_timing(tmp_path: Path) -> None:
    payload = _record()
    payload["seconds_to_first_success"] = None
    _write_record(tmp_path, payload)
    with pytest.raises(ValueError, match="successful run requires seconds_to_first_success"):
        onboarding_report.load_runs(tmp_path, SCHEMA)


def test_failed_run_cannot_claim_meaningful_receipt(tmp_path: Path) -> None:
    payload = _record()
    payload["first_success"] = False
    payload["seconds_to_first_success"] = None
    _write_record(tmp_path, payload)
    with pytest.raises(ValueError, match="unsuccessful run must use meaningful_receipt='none'"):
        onboarding_report.load_runs(tmp_path, SCHEMA)


def test_retention_aggregation_keeps_assisted_and_independent_separate(tmp_path: Path) -> None:
    independent = _record("retained-independent")
    independent["cohort"] = "persistent_integration"
    independent["retention"] = {
        "day7": "active_independent",
        "day30": "active_independent",
        "removed_reason": None,
    }
    assisted = _record("retained-assisted")
    assisted["cohort"] = "persistent_integration"
    assisted["retention"] = {
        "day7": "active_assisted",
        "day30": "active_assisted",
        "removed_reason": None,
    }
    _write_record(tmp_path, independent, "a.json")
    _write_record(tmp_path, assisted, "b.json")

    summary = onboarding_report.summarize(onboarding_report.load_runs(tmp_path, SCHEMA))
    assert summary["retention_day30"]["active_independent"] == 1
    assert summary["retention_day30"]["active_assisted"] == 1

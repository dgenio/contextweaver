"""Tests for privacy-safe D1 distribution and retention evidence."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import onboarding_report  # noqa: E402

SCHEMA = Path(__file__).parent.parent / "benchmarks" / "onboarding" / "schema.json"


def _record(run_id: str, **overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "schema_version": 1,
        "run_id": run_id,
        "participant_id": f"private-{run_id}",
        "synthetic": False,
        "cohort": "unassisted_evaluator",
        "proposition": "D1",
        "acquisition_path": "direct_outreach",
        "qualified_exposure": True,
        "understood_proposition": True,
        "chose_to_evaluate": False,
        "attempted_setup": False,
        "first_success": False,
        "seconds_to_first_success": None,
        "real_source": False,
        "maintainer_interventions": 0,
        "outcome": "declined",
        "dropoff_reason": "existing_alternative_sufficient",
        "retained_slice": "none",
        "retention": {"day7": "not_due", "day30": "not_due", "removed_reason": None},
        "contextweaver_version": "0.18.1",
        "commit_sha": None,
        "source_type": "none",
        "notes": f"secret note for {run_id}",
        "consent_public": False,
    }
    record.update(overrides)
    return record


def _write(directory: Path, record: dict[str, object]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{record['run_id']}.json").write_text(json.dumps(record), encoding="utf-8")


def test_decline_before_setup_is_valid_and_counted(tmp_path: Path) -> None:
    _write(tmp_path, _record("declined"))

    records = onboarding_report.load_records(tmp_path, SCHEMA)
    summary = onboarding_report.summarize(records)

    assert summary["included_records"] == 1
    funnel = summary["funnel"]
    assert isinstance(funnel, dict)
    assert funnel["qualified_exposure"]["count"] == 1
    assert funnel["understood_proposition"]["count"] == 1
    assert funnel["chose_to_evaluate"]["count"] == 0
    assert summary["dropoff_reasons"] == {"existing_alternative_sufficient": 1}


def test_first_success_and_retained_slice_are_separate_signals(tmp_path: Path) -> None:
    _write(
        tmp_path,
        _record(
            "success",
            chose_to_evaluate=True,
            attempted_setup=True,
            first_success=True,
            seconds_to_first_success=95,
            real_source=True,
            outcome="real_project",
            dropoff_reason="none",
            retained_slice="diff_only",
            source_type="openapi",
        ),
    )
    _write(
        tmp_path,
        _record(
            "retained",
            cohort="persistent_integration",
            acquisition_path="github",
            chose_to_evaluate=True,
            attempted_setup=True,
            first_success=True,
            seconds_to_first_success=40,
            real_source=True,
            outcome="retained",
            dropoff_reason="none",
            retained_slice="snapshot_and_diff",
            source_type="mcp",
            retention={
                "day7": "active_independent",
                "day30": "active_independent",
                "removed_reason": None,
            },
        ),
    )

    summary = onboarding_report.summarize(onboarding_report.load_records(tmp_path, SCHEMA))

    assert summary["real_project_records"] == 2
    assert summary["retained_slices"] == {"diff_only": 1, "snapshot_and_diff": 1}
    retention = summary["day30_retention"]
    assert isinstance(retention, dict)
    assert retention["active_independent"] == 1


def test_synthetic_fixture_is_excluded_by_default(tmp_path: Path) -> None:
    _write(tmp_path, _record("synthetic", synthetic=True))

    records = onboarding_report.load_records(tmp_path, SCHEMA)

    assert onboarding_report.summarize(records)["included_records"] == 0
    assert onboarding_report.summarize(records, include_synthetic=True)["included_records"] == 1


def test_report_never_renders_participant_id_or_notes(tmp_path: Path) -> None:
    _write(tmp_path, _record("private-person"))
    records = onboarding_report.load_records(tmp_path, SCHEMA)

    markdown = onboarding_report.render_markdown(onboarding_report.summarize(records))

    assert "private-private-person" not in markdown
    assert "secret note" not in markdown
    assert "existing_alternative_sufficient" in markdown


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"qualified_exposure": False, "understood_proposition": True},
            "understood_proposition requires qualified_exposure",
        ),
        (
            {"understood_proposition": False, "chose_to_evaluate": True},
            "chose_to_evaluate requires understood_proposition",
        ),
    ],
)
def test_non_monotonic_funnel_record_is_rejected(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    record = _record("invalid-funnel", **overrides)
    if record["chose_to_evaluate"]:
        record["outcome"] = "pending"
        record["dropoff_reason"] = "none"
    _write(tmp_path, record)

    with pytest.raises(ValueError, match=message):
        onboarding_report.load_records(tmp_path, SCHEMA)


def test_inconsistent_success_record_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        _record(
            "invalid",
            chose_to_evaluate=True,
            attempted_setup=False,
            first_success=True,
            seconds_to_first_success=12,
            outcome="first_success",
            dropoff_reason="none",
        ),
    )

    with pytest.raises(ValueError, match="first_success requires attempted_setup"):
        onboarding_report.load_records(tmp_path, SCHEMA)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"outcome": "declined", "dropoff_reason": "none"},
            "declined outcome requires a dropoff_reason",
        ),
        (
            {
                "chose_to_evaluate": True,
                "attempted_setup": True,
                "first_success": True,
                "seconds_to_first_success": 30,
                "outcome": "retained",
                "dropoff_reason": "value_not_consequential",
            },
            "dropoff_reason requires a declined, setup_failed, or removed outcome",
        ),
    ],
)
def test_dropoff_reason_must_match_negative_outcome(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    _write(tmp_path, _record("invalid-dropoff", **overrides))

    with pytest.raises(ValueError, match=message):
        onboarding_report.load_records(tmp_path, SCHEMA)


def test_main_writes_zero_real_evidence_report(tmp_path: Path) -> None:
    input_dir = tmp_path / "results"
    input_dir.mkdir()
    json_output = tmp_path / "report.json"
    markdown_output = tmp_path / "report.md"

    assert (
        onboarding_report.main(
            [
                "--input",
                str(input_dir),
                "--schema",
                str(SCHEMA),
                "--json-output",
                str(json_output),
                "--markdown-output",
                str(markdown_output),
            ]
        )
        == 0
    )
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["included_records"] == 0
    assert "Included real evidence records: **0**" in markdown_output.read_text(encoding="utf-8")

#!/usr/bin/env python3
"""Validate D1 distribution evidence and render privacy-safe aggregates.

The default report excludes synthetic fixtures and never renders participant IDs,
free-text notes, or removed-reason text. It exists to distinguish distribution,
onboarding, product-value, and retention failures for issues #855/#658/#551.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import cast

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA = ROOT / "benchmarks" / "onboarding" / "schema.json"
DEFAULT_INPUT = ROOT / "benchmarks" / "onboarding" / "results"
DEFAULT_JSON = ROOT / "benchmarks" / "onboarding" / "report.json"
DEFAULT_MARKDOWN = ROOT / "benchmarks" / "onboarding" / "report.md"

COHORTS = ("design_partner", "unassisted_evaluator", "persistent_integration")
FUNNEL_FIELDS = (
    "qualified_exposure",
    "understood_proposition",
    "chose_to_evaluate",
    "attempted_setup",
    "first_success",
)

JsonObject = dict[str, object]


def _json_object(path: Path) -> JsonObject:
    value = cast(object, json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return cast(JsonObject, value)


def _validate_consistency(path: Path, record: Mapping[str, object]) -> None:
    chose = bool(record["chose_to_evaluate"])
    attempted = bool(record["attempted_setup"])
    success = bool(record["first_success"])
    real_source = bool(record["real_source"])
    outcome = str(record["outcome"])
    seconds = record.get("seconds_to_first_success")

    if attempted and not chose:
        raise ValueError(f"{path}: attempted_setup requires chose_to_evaluate")
    if success and not attempted:
        raise ValueError(f"{path}: first_success requires attempted_setup")
    if success and seconds is None:
        raise ValueError(f"{path}: first_success requires seconds_to_first_success")
    if not success and seconds is not None:
        raise ValueError(f"{path}: seconds_to_first_success requires first_success")
    if real_source and not attempted:
        raise ValueError(f"{path}: real_source requires attempted_setup")
    if not chose and outcome != "declined":
        raise ValueError(f"{path}: non-evaluator outcome must be 'declined'")
    if outcome == "declined" and chose:
        raise ValueError(f"{path}: declined outcome requires chose_to_evaluate=false")
    if outcome == "setup_failed" and (not attempted or success):
        raise ValueError(f"{path}: setup_failed requires attempted setup without first success")
    if outcome in {"first_success", "real_project", "retained", "removed"} and not success:
        raise ValueError(f"{path}: {outcome} outcome requires first_success")


def load_records(input_dir: Path, schema_path: Path) -> list[JsonObject]:
    """Load, JSON-Schema validate, and cross-field validate evidence records."""
    schema = _json_object(schema_path)
    validator = Draft202012Validator(schema)
    records: list[JsonObject] = []
    if not input_dir.exists():
        return records

    seen_ids: set[str] = set()
    for path in sorted(input_dir.glob("*.json")):
        record = _json_object(path)
        errors = sorted(validator.iter_errors(record), key=lambda error: list(error.path))
        if errors:
            messages = [
                f"{path}: {'.'.join(map(str, error.path)) or '<root>'}: {error.message}"
                for error in errors
            ]
            raise ValueError("\n".join(messages))
        run_id = str(record["run_id"])
        if run_id in seen_ids:
            raise ValueError(f"duplicate evidence run_id {run_id!r}")
        seen_ids.add(run_id)
        _validate_consistency(path, record)
        records.append(record)
    return records


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator * 100, 2) if denominator else None


def _median(values: Iterable[float]) -> float | None:
    collected = list(values)
    return round(float(statistics.median(collected)), 2) if collected else None


def summarize(records: Sequence[JsonObject], *, include_synthetic: bool = False) -> JsonObject:
    """Return aggregates sufficient to interpret the D1 distribution funnel."""
    selected = [record for record in records if include_synthetic or not bool(record["synthetic"])]
    cohorts = Counter(str(record["cohort"]) for record in selected)
    acquisition = Counter(str(record["acquisition_path"]) for record in selected)
    dropoffs = Counter(
        str(record["dropoff_reason"])
        for record in selected
        if str(record["dropoff_reason"]) != "none"
    )
    slices = Counter(
        str(record.get("retained_slice", "none"))
        for record in selected
        if str(record.get("retained_slice", "none")) != "none"
    )

    funnel: JsonObject = {}
    previous_count = len(selected)
    for field in FUNNEL_FIELDS:
        count = sum(1 for record in selected if bool(record[field]))
        funnel[field] = {
            "count": count,
            "rate_from_previous_pct": _rate(count, previous_count),
        }
        previous_count = count

    success_records = [record for record in selected if bool(record["first_success"])]
    real_source_records = [record for record in selected if bool(record["real_source"])]
    real_project_records = [
        record
        for record in selected
        if str(record["outcome"]) in {"real_project", "retained", "removed"}
    ]
    persistent = [record for record in selected if record["cohort"] == "persistent_integration"]
    day30 = Counter(
        str(cast(Mapping[str, object], record["retention"])["day30"]) for record in persistent
    )

    return {
        "schema_version": 1,
        "included_records": len(selected),
        "synthetic_included": include_synthetic,
        "cohorts": {cohort: cohorts.get(cohort, 0) for cohort in COHORTS},
        "acquisition_paths": dict(sorted(acquisition.items())),
        "funnel": funnel,
        "first_success": {
            "records": len(success_records),
            "median_seconds": _median(
                float(cast(float | int, record["seconds_to_first_success"]))
                for record in success_records
            ),
            "real_source_records": len(real_source_records),
            "records_requiring_maintainer_help": sum(
                1 for record in selected if int(cast(int, record["maintainer_interventions"])) > 0
            ),
        },
        "real_project_records": len(real_project_records),
        "day30_retention": {
            "persistent_records": len(persistent),
            "active_independent": day30.get("active_independent", 0),
            "active_assisted": day30.get("active_assisted", 0),
            "removed": day30.get("removed", 0),
            "no_followup": day30.get("no_followup", 0),
            "not_due": day30.get("not_due", 0),
        },
        "dropoff_reasons": dict(sorted(dropoffs.items(), key=lambda item: (-item[1], item[0]))),
        "retained_slices": dict(sorted(slices.items(), key=lambda item: (-item[1], item[0]))),
    }


def _display(value: object, suffix: str = "") -> str:
    return "—" if value is None else f"{value}{suffix}"


def render_markdown(summary: Mapping[str, object]) -> str:
    """Render aggregates without participant-level or free-text data."""
    funnel = cast(Mapping[str, Mapping[str, object]], summary["funnel"])
    first_success = cast(Mapping[str, object], summary["first_success"])
    retention = cast(Mapping[str, object], summary["day30_retention"])
    cohorts = cast(Mapping[str, object], summary["cohorts"])
    acquisition = cast(Mapping[str, object], summary["acquisition_paths"])
    dropoffs = cast(Mapping[str, object], summary["dropoff_reasons"])
    slices = cast(Mapping[str, object], summary["retained_slices"])

    lines = [
        "# ContextWeaver D1 distribution evidence",
        "",
        "> Privacy-safe aggregate. Synthetic fixtures are excluded by default.",
        "> Participant IDs, notes, and removed-reason text are never rendered.",
        "",
        f"Included real evidence records: **{summary['included_records']}**",
        "",
        "## Cohorts",
        "",
        "| cohort | records |",
        "|---|---:|",
    ]
    for cohort in COHORTS:
        lines.append(f"| `{cohort}` | {cohorts[cohort]} |")

    lines.extend(
        ["", "## Distribution funnel", "", "| stage | count | from previous |", "|---|---:|---:|"]
    )
    for field in FUNNEL_FIELDS:
        stage = funnel[field]
        lines.append(
            f"| `{field}` | {stage['count']} | {_display(stage['rate_from_previous_pct'], '%')} |"
        )

    lines.extend(
        [
            "",
            "## First success / real use",
            "",
            f"- First-success records: **{first_success['records']}**",
            f"- Median seconds to first success: **{_display(first_success['median_seconds'])}**",
            f"- Real-source records: **{first_success['real_source_records']}**",
            "- Records requiring maintainer help: "
            f"**{first_success['records_requiring_maintainer_help']}**",
            f"- Real-project records: **{summary['real_project_records']}**",
            "",
            "## 30-day retention",
            "",
            f"- Persistent-integration records: **{retention['persistent_records']}**",
            f"- Active independently: **{retention['active_independent']}**",
            f"- Active with maintainer help: **{retention['active_assisted']}**",
            f"- Removed: **{retention['removed']}**",
            f"- No follow-up: **{retention['no_followup']}**",
            f"- Not due: **{retention['not_due']}**",
            "",
            "## Acquisition paths",
            "",
        ]
    )
    lines.extend(_mapping_table(acquisition, "path"))
    lines.extend(["", "## Drop-off reasons", ""])
    lines.extend(_mapping_table(dropoffs, "reason"))
    lines.extend(["", "## Retained slices", ""])
    lines.extend(_mapping_table(slices, "slice"))
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "Zero adopters without qualified exposure is distribution-inconclusive, not product failure.",
            "Repeated first success followed by removal is stronger product/value failure evidence.",
            "If users retain only a narrow slice, that slice — not the historical architecture —",
            "becomes the candidate product. See issues #758 and #855.",
            "",
        ]
    )
    return "\n".join(lines)


def _mapping_table(values: Mapping[str, object], heading: str) -> list[str]:
    if not values:
        return ["_No records yet._"]
    lines = [f"| {heading} | count |", "|---|---:|"]
    lines.extend(f"| `{key}` | {value} |" for key, value in values.items())
    return lines


def write_reports(summary: JsonObject, json_path: Path, markdown_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(summary), encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--include-synthetic", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        records = load_records(args.input, args.schema)
        summary = summarize(records, include_synthetic=args.include_synthetic)
        write_reports(summary, args.json_output, args.markdown_output)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"Validated {len(records)} evidence record(s); rendered "
        f"{summary['included_records']} included record(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

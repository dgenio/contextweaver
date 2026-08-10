#!/usr/bin/env python3
"""Validate onboarding evidence and render privacy-safe aggregate reports.

The report deliberately separates cohorts and excludes synthetic fixtures by
default. It never renders participant IDs or free-text notes, so the generated
summary can be committed without turning research records into testimonials.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA = ROOT / "benchmarks" / "onboarding" / "schema.json"
DEFAULT_INPUT = ROOT / "benchmarks" / "onboarding" / "results"
DEFAULT_JSON = ROOT / "benchmarks" / "onboarding" / "report.json"
DEFAULT_MARKDOWN = ROOT / "benchmarks" / "onboarding" / "report.md"

COHORTS = ("design_partner", "unassisted_evaluator", "persistent_integration")


def load_runs(input_dir: Path, schema_path: Path) -> list[dict[str, Any]]:
    """Load and validate every JSON evidence record under *input_dir*."""
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    runs: list[dict[str, Any]] = []
    if not input_dir.exists():
        return runs

    seen_ids: set[str] = set()
    for path in sorted(input_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.path))
        if errors:
            messages = [f"{path}: {'.'.join(map(str, error.path)) or '<root>'}: {error.message}" for error in errors]
            raise ValueError("\n".join(messages))
        run_id = str(payload["run_id"])
        if run_id in seen_ids:
            raise ValueError(f"duplicate onboarding run_id {run_id!r}")
        seen_ids.add(run_id)
        if payload["first_success"] and payload.get("seconds_to_first_success") is None:
            raise ValueError(f"{path}: successful run requires seconds_to_first_success")
        if not payload["first_success"] and payload["meaningful_receipt"] != "none":
            raise ValueError(f"{path}: unsuccessful run must use meaningful_receipt='none'")
        runs.append(payload)
    return runs


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator * 100, 2) if denominator else None


def _median(values: Iterable[float]) -> float | None:
    collected = list(values)
    return round(float(statistics.median(collected)), 2) if collected else None


def summarize(runs: list[dict[str, Any]], *, include_synthetic: bool = False) -> dict[str, Any]:
    """Return privacy-safe aggregate metrics for validated evidence records."""
    selected = [run for run in runs if include_synthetic or not bool(run["synthetic"])]
    cohort_counts = Counter(str(run["cohort"]) for run in selected)
    failure_counts: Counter[str] = Counter()
    for run in selected:
        failure_counts.update(str(value) for value in run.get("failures", []))

    unassisted = [run for run in selected if run["cohort"] == "unassisted_evaluator"]
    unassisted_success = [run for run in unassisted if bool(run["first_success"])]
    real_source_unassisted = [run for run in unassisted if bool(run["real_source"])]
    real_source_success = [run for run in real_source_unassisted if bool(run["first_success"])]

    integrations = [run for run in selected if run["cohort"] == "persistent_integration"]
    day30 = Counter(str(run["retention"]["day30"]) for run in integrations)

    return {
        "schema_version": 1,
        "included_runs": len(selected),
        "synthetic_included": include_synthetic,
        "cohorts": {cohort: cohort_counts.get(cohort, 0) for cohort in COHORTS},
        "unassisted": {
            "runs": len(unassisted),
            "successful": len(unassisted_success),
            "success_rate_pct": _rate(len(unassisted_success), len(unassisted)),
            "median_seconds_to_first_success": _median(
                float(run["seconds_to_first_success"])
                for run in unassisted_success
                if run.get("seconds_to_first_success") is not None
            ),
            "real_source_runs": len(real_source_unassisted),
            "real_source_success_rate_pct": _rate(
                len(real_source_success), len(real_source_unassisted)
            ),
            "runs_requiring_maintainer_intervention": sum(
                1 for run in unassisted if int(run["maintainer_interventions"]) > 0
            ),
        },
        "retention_day30": {
            "integration_runs": len(integrations),
            "active_independent": day30.get("active_independent", 0),
            "active_assisted": day30.get("active_assisted", 0),
            "removed": day30.get("removed", 0),
            "no_followup": day30.get("no_followup", 0),
            "not_due": day30.get("not_due", 0),
        },
        "failure_counts": dict(sorted(failure_counts.items(), key=lambda item: (-item[1], item[0]))),
    }


def render_markdown(summary: dict[str, Any]) -> str:
    """Render a human-readable aggregate without participant-level data."""
    unassisted = summary["unassisted"]
    retention = summary["retention_day30"]

    def value_or_dash(value: object, suffix: str = "") -> str:
        return "—" if value is None else f"{value}{suffix}"

    lines = [
        "# ContextWeaver onboarding evidence",
        "",
        "> Generated aggregate only. Participant IDs, notes and proprietary details are never rendered.",
        "> Synthetic fixtures are excluded from the default report.",
        "",
        f"Included real evidence runs: **{summary['included_runs']}**",
        "",
        "## Cohorts",
        "",
        "| cohort | runs |",
        "|---|---:|",
    ]
    for cohort in COHORTS:
        lines.append(f"| `{cohort}` | {summary['cohorts'][cohort]} |")

    lines.extend(
        [
            "",
            "## Unassisted first success",
            "",
            f"- Runs: **{unassisted['runs']}**",
            f"- Successful: **{unassisted['successful']}**",
            f"- Success rate: **{value_or_dash(unassisted['success_rate_pct'], '%')}**",
            "- Median seconds to first meaningful receipt: "
            f"**{value_or_dash(unassisted['median_seconds_to_first_success'])}**",
            f"- Real-source runs: **{unassisted['real_source_runs']}**",
            "- Real-source success rate: "
            f"**{value_or_dash(unassisted['real_source_success_rate_pct'], '%')}**",
            "- Runs requiring maintainer intervention: "
            f"**{unassisted['runs_requiring_maintainer_intervention']}**",
            "",
            "## 30-day integration retention",
            "",
            f"- Integration evidence runs: **{retention['integration_runs']}**",
            f"- Active independently: **{retention['active_independent']}**",
            f"- Active with maintainer help: **{retention['active_assisted']}**",
            f"- Removed: **{retention['removed']}**",
            f"- No follow-up: **{retention['no_followup']}**",
            f"- Not due yet: **{retention['not_due']}**",
            "",
            "## Failure taxonomy",
            "",
        ]
    )
    failures = summary["failure_counts"]
    if failures:
        lines.extend(["| failure category | count |", "|---|---:|"])
        for category, count in failures.items():
            lines.append(f"| `{category}` | {count} |")
    else:
        lines.append("_No real failure records yet._")

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "This report is a product-decision input, not a growth dashboard. A successful",
            "synthetic demo, star, fork or download is not adoption evidence. Keep design-partner",
            "findings separate from unassisted onboarding, and treat removed integrations as useful",
            "negative evidence for the go / narrow / stop decision in issue #758.",
            "",
        ]
    )
    return "\n".join(lines)


def write_reports(summary: dict[str, Any], json_path: Path, markdown_path: Path) -> None:
    """Write deterministic JSON and Markdown aggregate reports."""
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(summary), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--include-synthetic", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        runs = load_runs(args.input, args.schema)
        summary = summarize(runs, include_synthetic=args.include_synthetic)
        write_reports(summary, args.json_output, args.markdown_output)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Validated {len(runs)} evidence run(s); rendered aggregate for {summary['included_runs']} run(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

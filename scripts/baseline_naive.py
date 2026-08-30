#!/usr/bin/env python3
"""Naïve-concat baseline harness (issue #215, evidence fix #841).

Computes the estimated token cost and a coverage proxy for a "dump everything"
baseline. The reduction ratio is intentionally measured with the *same*
estimator as the ContextWeaver context benchmark. Mixing a tiktoken count for
the naïve arm with a heuristic count for the ContextWeaver arm produced an
invalid ratio and made historical results environment-dependent (#841).

Two roles:

1. ``compute_naive_delta`` is invoked from ``benchmarks/benchmark.py`` per
   scenario row.
2. The standalone CLI can annotate an existing compatible benchmark JSON.

The deterministic release-history method is ``heuristic/chardiv4`` for both
arms. Exact tokenizer/provider counts may be reported by separate benchmarks,
but a comparison must never silently switch measurement methods.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
_CATALOG_PATH = _ROOT / "examples" / "sample_catalog.json"
sys.path.insert(0, str(_ROOT / "src"))

from contextweaver.protocols import CharDivFourEstimator  # noqa: E402

_ESTIMATOR = CharDivFourEstimator()
ESTIMATOR_ID = _ESTIMATOR.name


def _count_estimated_tokens(text: str) -> int:
    """Return the deterministic release-benchmark estimate for *text*."""
    return _ESTIMATOR.estimate(text)


def _render_catalog_schema(catalog_path: Path) -> str:
    """Render the canonical sample catalog as a naïve all-tools blob."""
    items: list[dict[str, Any]] = json.loads(catalog_path.read_text(encoding="utf-8"))
    parts: list[str] = []
    for item in sorted(items, key=lambda value: str(value.get("id", ""))):
        item_id = str(item.get("id", ""))
        description = str(item.get("description", "") or item.get("name", ""))
        tags = ",".join(str(tag) for tag in item.get("tags", []) or [])
        parts.append(f"{item_id}: {description} [tags: {tags}]")
    return "\n".join(parts)


def _scenario_text(scenario_path: Path) -> str:
    """Concatenate every scenario ``text`` field in source order."""
    parts: list[str] = []
    for line in scenario_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        text = event.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def compute_naive_delta(scenario_path: Path, context_row: dict[str, Any]) -> dict[str, Any]:
    """Compute a same-unit naïve-vs-ContextWeaver reduction block.

    New context rows may identify the estimator used for ``prompt_tokens``.
    When they do, a mismatch fails closed. Older rows omitted that metadata;
    the benchmark that calls this helper is known to use the same
    ``CharDivFourEstimator`` and this function records the method explicitly in
    its output so the ambiguity does not propagate to new evidence.
    """
    row_estimator = str(context_row.get("token_estimator", ""))
    if row_estimator and row_estimator != ESTIMATOR_ID:
        raise ValueError(
            "naive baseline estimator mismatch: "
            f"context row uses {row_estimator!r}, baseline uses {ESTIMATOR_ID!r}"
        )

    catalog_blob = _render_catalog_schema(_CATALOG_PATH)
    scenario_blob = _scenario_text(scenario_path)
    naive_tokens = _count_estimated_tokens(catalog_blob + "\n" + scenario_blob)
    cw_tokens = int(context_row.get("prompt_tokens", 0))
    pct_reduction = round((1.0 - cw_tokens / naive_tokens) * 100, 2) if naive_tokens else 0.0
    event_count = max(int(context_row.get("event_count", 0)), 1)
    items_included = int(context_row.get("items_included", 0))
    coverage_pct = round(items_included / event_count * 100, 2)
    return {
        "token_estimator": ESTIMATOR_ID,
        "naive_tokens": naive_tokens,
        "cw_tokens": cw_tokens,
        "pct_reduction": float(pct_reduction),
        "coverage_pct": float(coverage_pct),
    }


def annotate_latest_json(latest_path: Path, scenarios_dir: Path) -> int:
    """Augment a compatible benchmark JSON with deterministic naïve deltas."""
    payload = json.loads(latest_path.read_text(encoding="utf-8"))
    rows = payload.get("context")
    if not isinstance(rows, list):
        print(f"benchmark JSON: no context list at {latest_path}", file=sys.stderr)
        return 0

    annotated = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("scenario", ""))
        path = scenarios_dir / f"{name}.jsonl"
        if not path.is_file():
            print(f"skip: no scenario JSONL for {name!r}", file=sys.stderr)
            continue
        row["naive_delta"] = compute_naive_delta(path, row)
        annotated += 1

    latest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return annotated


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--latest",
        default=str(_ROOT / "benchmarks" / "results" / "latest.json"),
        help="Benchmark JSON to annotate in place.",
    )
    parser.add_argument(
        "--scenarios-dir",
        default=str(_ROOT / "benchmarks" / "scenarios"),
        help="Directory containing the scenario JSONL files.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    latest = Path(args.latest)
    scenarios = Path(args.scenarios_dir)
    if not latest.is_file():
        print(f"benchmark JSON not found: {latest}", file=sys.stderr)
        return 1
    if not scenarios.is_dir():
        print(f"scenarios dir not found: {scenarios}", file=sys.stderr)
        return 1
    try:
        count = annotate_latest_json(latest, scenarios)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Annotated {count} context row(s) with naive_delta in {latest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

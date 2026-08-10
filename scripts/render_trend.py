#!/usr/bin/env python3
"""Render release-over-release benchmark trends with methodology boundaries.

Release snapshots retain deterministic routing/context metrics while excluding
runner-dependent latency.  Snapshot schema v2 adds the token-estimator identity
used by the naïve-vs-ContextWeaver reduction metric.  Earlier schema-v1 token
reduction values are kept for history but rendered as legacy/unverified rather
than pretending they are directly comparable (#841).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from _golden import check_text_artifacts, write_text_artifacts

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HISTORY_DIR = REPO_ROOT / "benchmarks" / "results" / "history"
DEFAULT_OUTPUT = REPO_ROOT / "benchmarks" / "trend.md"
DEFAULT_LATEST = REPO_ROOT / "benchmarks" / "results" / "latest.json"

SNAPSHOT_SCHEMA_VERSION = 2


def _reduction_method(latest: dict[str, Any]) -> str:
    """Return the single explicit estimator used by every reduction row.

    New evidence fails closed when method metadata is missing or inconsistent.
    That prevents a release snapshot from repeating the mixed-estimator bug
    discovered while preparing v0.18.1.
    """
    methods: set[str] = set()
    rows_with_reduction = 0
    for row in latest.get("context", []):
        if not isinstance(row, dict):
            continue
        delta = row.get("naive_delta")
        if not isinstance(delta, dict) or "pct_reduction" not in delta:
            continue
        rows_with_reduction += 1
        method = delta.get("token_estimator")
        if not isinstance(method, str) or not method:
            raise ValueError("naive_delta is missing explicit token_estimator metadata")
        methods.add(method)
    if rows_with_reduction and len(methods) != 1:
        raise ValueError(f"token-reduction rows use inconsistent estimators: {sorted(methods)}")
    return next(iter(methods), "none")


def extract_snapshot(release: str, latest: dict[str, Any]) -> dict[str, Any]:
    """Return the deterministic metric subset for one release.

    Token-reduction rows must identify one measurement method. Latency remains
    intentionally excluded from release history.
    """
    recall: dict[str, float] = {}
    mrr: dict[str, float] = {}
    precision: dict[str, float] = {}
    for row in latest.get("routing", []):
        if not isinstance(row, dict):
            continue
        size = str(int(row.get("catalog_size", 0)))
        recall[size] = round(float(row.get("recall_at_k", 0.0)), 4)
        mrr[size] = round(float(row.get("mrr", 0.0)), 4)
        precision[size] = round(float(row.get("precision_at_k", 0.0)), 4)

    method = _reduction_method(latest)
    reductions: list[float] = []
    dropped = 0
    dedup = 0
    for row in latest.get("context", []):
        if not isinstance(row, dict):
            continue
        dropped += int(row.get("items_dropped", 0))
        dedup += int(row.get("dedup_removed", 0))
        delta = row.get("naive_delta")
        if isinstance(delta, dict) and "pct_reduction" in delta:
            reductions.append(float(delta["pct_reduction"]))
    mean_reduction = round(sum(reductions) / len(reductions), 2) if reductions else 0.0

    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "release": release,
        "measurement": {
            "token_reduction_estimator": method,
            "token_reduction_unit": "estimated_tokens" if method != "none" else "none",
            "comparable_with_schema_v1_token_reduction": False,
        },
        "metrics": {
            "routing_recall_at_k": recall,
            "routing_mrr": mrr,
            "routing_precision_at_k": precision,
            "mean_token_reduction_pct": mean_reduction,
            "total_items_dropped": dropped,
            "total_dedup_removed": dedup,
        },
    }


def write_snapshot(snapshot: dict[str, Any], history_dir: Path) -> Path:
    """Write *snapshot* deterministically to ``<history_dir>/<release>.json``."""
    history_dir.mkdir(parents=True, exist_ok=True)
    out = history_dir / f"{snapshot['release']}.json"
    out.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return out


def _version_key(release: str) -> tuple[Any, ...]:
    """Best-effort semantic ordering: numeric tuple, with string fallback."""
    parts: list[Any] = []
    for chunk in release.split("."):
        parts.append((0, int(chunk)) if chunk.isdigit() else (1, chunk))
    return tuple(parts)


def load_history(history_dir: Path) -> list[dict[str, Any]]:
    """Load every snapshot ordered oldest release first."""
    snapshots: list[dict[str, Any]] = []
    if not history_dir.exists():
        return snapshots
    for path in sorted(history_dir.glob("*.json")):
        snapshots.append(json.loads(path.read_text(encoding="utf-8")))
    return sorted(snapshots, key=lambda snapshot: _version_key(str(snapshot.get("release", ""))))


def _per_size_table(snapshots: list[dict[str, Any]], metric: str) -> str:
    sizes = sorted(
        {int(size) for snapshot in snapshots for size in snapshot.get("metrics", {}).get(metric, {})}
    )
    if not sizes:
        return "_No data._"
    header = "| release | " + " | ".join(f"size={size}" for size in sizes) + " |"
    sep = "|---|" + "---:|" * len(sizes)
    lines = [header, sep]
    for snapshot in snapshots:
        values = snapshot.get("metrics", {}).get(metric, {})
        cells = [
            f"{float(values[str(size)]):.4f}" if str(size) in values else "—" for size in sizes
        ]
        lines.append(f"| `{snapshot.get('release', '?')}` | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _measurement_label(snapshot: dict[str, Any]) -> str:
    if int(snapshot.get("schema_version", 1)) < 2:
        return "legacy/unverified†"
    measurement = snapshot.get("measurement", {})
    if not isinstance(measurement, dict):
        return "unknown"
    return str(measurement.get("token_reduction_estimator", "unknown"))


def _context_table(snapshots: list[dict[str, Any]]) -> str:
    header = (
        "| release | mean estimated token reduction | estimator | items dropped | dedup removed |"
    )
    sep = "|---|---:|---|---:|---:|"
    lines = [header, sep]
    for snapshot in snapshots:
        metrics = snapshot.get("metrics", {})
        lines.append(
            f"| `{snapshot.get('release', '?')}` | "
            f"{float(metrics.get('mean_token_reduction_pct', 0.0)):.2f}% | "
            f"`{_measurement_label(snapshot)}` | "
            f"{int(metrics.get('total_items_dropped', 0))} | "
            f"{int(metrics.get('total_dedup_removed', 0))} |"
        )
    return "\n".join(lines)


def render(snapshots: list[dict[str, Any]]) -> str:
    """Return deterministic trend Markdown for *snapshots*."""
    parts = [
        "# contextweaver — Benchmark Trend",
        "",
        "> Auto-generated by `make trend`. Do not edit by hand.",
        "> Source: `benchmarks/results/history/*.json` (one snapshot per release).",
        "",
        "Release-over-release view of deterministic benchmark metrics. Latency is excluded",
        "because it is environment-dependent. Token-reduction comparisons are only directly",
        "comparable when the snapshot records the same estimator methodology.",
        "",
    ]
    if not snapshots:
        parts.extend(
            [
                "_No release snapshots recorded yet. Capture one with_",
                "`python scripts/render_trend.py --snapshot <version> "
                "--from <benchmark.json>`.",
                "",
            ]
        )
        return "\n".join(parts)

    parts.extend(
        [
            f"Releases recorded: {len(snapshots)} "
            f"(`{snapshots[0].get('release', '?')}` … `{snapshots[-1].get('release', '?')}`).",
            "",
            "## Routing recall@k by catalog size",
            "",
            _per_size_table(snapshots, "routing_recall_at_k"),
            "",
            "## Routing MRR by catalog size",
            "",
            _per_size_table(snapshots, "routing_mrr"),
            "",
            "## Routing precision@k by catalog size",
            "",
            _per_size_table(snapshots, "routing_precision_at_k"),
            "",
            "## Context pipeline quality",
            "",
            _context_table(snapshots),
            "",
            "† Schema-v1 token-reduction snapshots predate enforced estimator identity.",
            "Their values are retained as historical evidence but are not treated as directly",
            "comparable with schema-v2 measurements.",
            "",
            "---",
            "",
            "## Capturing a release snapshot",
            "",
            "```bash",
            "python benchmarks/benchmark.py --output /tmp/contextweaver-release-benchmark.json",
            "python scripts/render_trend.py --snapshot <version> \\",
            "    --from /tmp/contextweaver-release-benchmark.json",
            "make trend",
            "```",
            "",
        ]
    )
    return "\n".join(parts)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else None,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--history-dir", default=str(DEFAULT_HISTORY_DIR))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--snapshot", help="Capture a release snapshot under this version, then exit")
    parser.add_argument("--from", dest="from_path", default=str(DEFAULT_LATEST))
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write; exit non-zero if benchmarks/trend.md would change.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    history_dir = Path(args.history_dir)

    if args.snapshot:
        latest_path = Path(args.from_path)
        if not latest_path.exists():
            print(f"error: {latest_path} not found — run the benchmark first.", file=sys.stderr)
            return 1
        latest = json.loads(latest_path.read_text(encoding="utf-8"))
        try:
            snapshot = extract_snapshot(args.snapshot, latest)
        except ValueError as exc:
            print(f"error: cannot capture benchmark snapshot: {exc}", file=sys.stderr)
            return 1
        out = write_snapshot(snapshot, history_dir)
        print(f"Wrote {out}")
        return 0

    rendered = {Path(args.output): render(load_history(history_dir))}
    if args.check:
        return check_text_artifacts(rendered, label="trend", regen="make trend")
    write_text_artifacts(rendered)
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

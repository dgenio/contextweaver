#!/usr/bin/env python3
"""Render the release-over-release benchmark trend page (issue #554).

The deterministic benchmark answers "what are the numbers *now*?"
(``benchmarks/results/latest.json`` + ``scorecard.md``). A regression that
creeps in over several releases is invisible in any single snapshot. This
script keeps a small, deterministic-only metric snapshot per release under
``benchmarks/results/history/<version>.json`` and renders the longitudinal
view to ``benchmarks/trend.md`` so quality trajectories stay visible.

Latency is deliberately excluded from snapshots — it is environment-dependent
and not comparable across release machines. This page is *visibility*, not a
gate; PR-time gating with tolerance bands is owned by ``benchmark_gate.py`` (#491).

The script is stdlib-only (no contextweaver import) so it can run before the
package is installed, matching ``scripts/render_scorecard.py``.

Usage::

    # Capture a release snapshot from the current latest.json:
    python scripts/render_trend.py --snapshot 0.16.0 --from benchmarks/results/latest.json
    python scripts/render_trend.py            # render benchmarks/trend.md
    python scripts/render_trend.py --check     # exit non-zero on drift
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

SNAPSHOT_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Snapshot extraction (latest.json -> deterministic metric subset)
# ---------------------------------------------------------------------------


def extract_snapshot(release: str, latest: dict[str, Any]) -> dict[str, Any]:
    """Return the deterministic-only metric subset for one release.

    Latency fields are intentionally omitted. The shape carries a
    ``schema_version`` so future metric changes do not orphan old entries.
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

    reductions: list[float] = []
    dropped = 0
    dedup = 0
    for row in latest.get("context", []):
        if not isinstance(row, dict):
            continue
        dropped += int(row.get("items_dropped", 0))
        dedup += int(row.get("dedup_removed", 0))
        nd = row.get("naive_delta")
        if isinstance(nd, dict) and "pct_reduction" in nd:
            reductions.append(float(nd["pct_reduction"]))
    mean_reduction = round(sum(reductions) / len(reductions), 2) if reductions else 0.0

    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "release": release,
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
    text = json.dumps(snapshot, indent=2, sort_keys=True) + "\n"
    out.write_text(text, encoding="utf-8", newline="\n")
    return out


# ---------------------------------------------------------------------------
# History loading + ordering
# ---------------------------------------------------------------------------


def _version_key(release: str) -> tuple[Any, ...]:
    """Best-effort semantic ordering: numeric tuple, with a string fallback."""
    parts: list[Any] = []
    for chunk in release.split("."):
        parts.append((0, int(chunk)) if chunk.isdigit() else (1, chunk))
    return tuple(parts)


def load_history(history_dir: Path) -> list[dict[str, Any]]:
    """Load every snapshot in *history_dir*, ordered oldest release first."""
    snapshots: list[dict[str, Any]] = []
    if not history_dir.exists():
        return snapshots
    for path in sorted(history_dir.glob("*.json")):
        snapshots.append(json.loads(path.read_text(encoding="utf-8")))
    return sorted(snapshots, key=lambda s: _version_key(str(s.get("release", ""))))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _per_size_table(snapshots: list[dict[str, Any]], metric: str) -> str:
    sizes = sorted({int(size) for s in snapshots for size in s.get("metrics", {}).get(metric, {})})
    if not sizes:
        return "_No data._"
    header = "| release | " + " | ".join(f"size={n}" for n in sizes) + " |"
    sep = "|---|" + "---:|" * len(sizes)
    lines = [header, sep]
    for s in snapshots:
        values = s.get("metrics", {}).get(metric, {})
        cells = [f"{float(values[str(n)]):.4f}" if str(n) in values else "—" for n in sizes]
        lines.append(f"| `{s.get('release', '?')}` | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _context_table(snapshots: list[dict[str, Any]]) -> str:
    header = "| release | mean token reduction | items dropped | dedup removed |"
    sep = "|---|---:|---:|---:|"
    lines = [header, sep]
    for s in snapshots:
        m = s.get("metrics", {})
        lines.append(
            f"| `{s.get('release', '?')}` | {float(m.get('mean_token_reduction_pct', 0.0)):.2f}% "
            f"| {int(m.get('total_items_dropped', 0))} | {int(m.get('total_dedup_removed', 0))} |"
        )
    return "\n".join(lines)


def render(snapshots: list[dict[str, Any]]) -> str:
    """Return the deterministic trend markdown for *snapshots*."""
    parts = [
        "# contextweaver — Benchmark Trend",
        "",
        "> Auto-generated by `make trend`. Do not edit by hand.",
        "> Source: `benchmarks/results/history/*.json` (one snapshot per release).",
        "",
        "Release-over-release view of the deterministic benchmark metrics. Latency",
        "is excluded — it is environment-dependent and not comparable across release",
        "machines. This page is visibility only; PR-time regression gating lives in",
        "`benchmarks/gating.yaml` + `scripts/benchmark_gate.py` (#491).",
        "",
    ]
    if not snapshots:
        parts.extend(
            [
                "_No release snapshots recorded yet. Capture one with_",
                "`python scripts/render_trend.py --snapshot <version> "
                "--from benchmarks/results/latest.json`.",
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
            "---",
            "",
            "## Capturing a release snapshot",
            "",
            "```bash",
            "make benchmark   # refresh benchmarks/results/latest.json",
            "python scripts/render_trend.py --snapshot <version> \\",
            "    --from benchmarks/results/latest.json",
            "make trend       # re-render benchmarks/trend.md",
            "```",
            "",
        ]
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else None,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--history-dir", default=str(DEFAULT_HISTORY_DIR))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--snapshot", help="Capture a release snapshot under this version, then exit"
    )
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
            print(f"error: {latest_path} not found — run `make benchmark` first.", file=sys.stderr)
            return 1
        latest = json.loads(latest_path.read_text(encoding="utf-8"))
        out = write_snapshot(extract_snapshot(args.snapshot, latest), history_dir)
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

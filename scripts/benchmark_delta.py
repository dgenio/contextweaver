#!/usr/bin/env python3
"""Render a markdown PR-comment delta between two ``latest.json`` snapshots.

Issue #211. Used by the CI step that posts a sticky regression-feedback
comment on every PR that touches routing / scoring code. The script is
**stdlib-only** so it can run before ``pip install -e .`` finishes; the
output is deterministic given the same head/base inputs (same delta bytes
on every run).

Convention shared with ``scripts/render_scorecard.py``:

- ✅ when ``head <= base × 1.30`` for latency cells (Round 2 Q5=C).
- ⚠️ when ``head > base × 1.30``.
- ✅ when ``head >= base - 1pp`` for accuracy cells.
- ⚠️ when ``head < base - 1pp``.

Usage::

    python scripts/benchmark_delta.py --base base.json --head head.json
    python scripts/benchmark_delta.py --base base.json --head head.json \\
        --output delta.md
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

# Latency-budget marker convention (Round 2 Q5=C).
LATENCY_BUDGET_MULTIPLIER = 1.30
# Accuracy regression bound, in pp (i.e. 1.0 means "-1pp").
ACCURACY_REGRESSION_PP = 1.0


# ---------------------------------------------------------------------------
# Marker helpers
# ---------------------------------------------------------------------------


def _latency_marker(base: float, head: float) -> str:
    """Return ✅ / ⚠️ for a latency-style cell (lower is better)."""
    if base <= 0:
        return "✅"
    return "⚠️" if head > base * LATENCY_BUDGET_MULTIPLIER else "✅"


def _accuracy_marker(base: float, head: float) -> str:
    """Return ✅ / ⚠️ for an accuracy-style cell (higher is better)."""
    delta_pp = (head - base) * 100
    return "⚠️" if delta_pp < -ACCURACY_REGRESSION_PP else "✅"


def _fmt_delta(base: float, head: float, decimals: int = 4) -> str:
    """Format ``head`` plus a signed delta vs ``base`` (e.g. ``"0.5200 (+0.02)"``)."""
    delta = head - base
    sign = "+" if delta >= 0 else ""
    return f"{head:.{decimals}f} ({sign}{delta:.{decimals}f})"


# ---------------------------------------------------------------------------
# Row-keyed views
# ---------------------------------------------------------------------------


def _routing_summary_index(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    rows = payload.get("routing", [])
    return {int(r["catalog_size"]): r for r in rows if isinstance(r, dict)}


def _matrix_index(payload: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    rows = payload.get("routing_matrix", [])
    return {
        (str(r.get("backend", "")), int(r.get("catalog_size", 0))): r
        for r in rows
        if isinstance(r, dict)
    }


def _context_index(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = payload.get("context", [])
    return {str(r.get("scenario", "")): r for r in rows if isinstance(r, dict)}


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------


def _render_routing_section(base: dict[str, Any], head: dict[str, Any]) -> str:
    """Render the single-backend ``routing`` delta table."""
    base_idx = _routing_summary_index(base)
    head_idx = _routing_summary_index(head)
    keys = sorted(set(base_idx) | set(head_idx))
    if not keys:
        return ""
    lines = [
        "### Routing summary (single backend × catalog sizes)",
        "",
        "| size | recall@k (head Δ vs base) | MRR (head Δ vs base) | p99 (ms) |",
        "|---:|---|---|---|",
    ]
    for size in keys:
        b = base_idx.get(size, {})
        h = head_idx.get(size, b)
        br = float(b.get("recall_at_k", 0.0))
        hr = float(h.get("recall_at_k", 0.0))
        bm = float(b.get("mrr", 0.0))
        hm = float(h.get("mrr", 0.0))
        bp = float(b.get("latency_ms_p99", 0.0))
        hp = float(h.get("latency_ms_p99", 0.0))
        lines.append(
            f"| {size} | {_accuracy_marker(br, hr)} {_fmt_delta(br, hr)} "
            f"| {_accuracy_marker(bm, hm)} {_fmt_delta(bm, hm)} "
            f"| {_latency_marker(bp, hp)} {hp:.3f} (base {bp:.3f}) |"
        )
    return "\n".join(lines)


def _render_matrix_section(base: dict[str, Any], head: dict[str, Any]) -> str:
    """Render the per-backend × per-size matrix delta table (#208 surface)."""
    base_idx = _matrix_index(base)
    head_idx = _matrix_index(head)
    keys = sorted(set(base_idx) | set(head_idx))
    if not keys:
        return ""
    lines = [
        "### Per-backend × per-size matrix",
        "",
        "| backend | size | recall@k (Δ) | MRR (Δ) | p99 (ms) |",
        "|---|---:|---|---|---|",
    ]
    for backend, size in keys:
        b = base_idx.get((backend, size), {})
        h = head_idx.get((backend, size), b)
        # Skip-cells carry zeroed metrics by design (e.g. fuzzy with no
        # rapidfuzz, status="skipped: ..."). Treating them as accuracy/
        # latency regressions would produce false-positive ⚠️ markers on
        # every PR; emit a single "skipped" row that surfaces the reason
        # instead. Real cells carry status="ok" (the MatrixCell default in
        # benchmarks/benchmark.py), so the gate is status != "ok",
        # aligning with scripts/render_scorecard.py's existing convention.
        head_status = str(h.get("status", "ok"))
        base_status = str(b.get("status", "ok"))
        if head_status != "ok" or base_status != "ok":
            reason = head_status if head_status != "ok" else base_status
            lines.append(f"| {backend} | {size} | _skipped_ ({reason}) | — | — |")
            continue
        br = float(b.get("recall_at_k", 0.0))
        hr = float(h.get("recall_at_k", 0.0))
        bm = float(b.get("mrr", 0.0))
        hm = float(h.get("mrr", 0.0))
        bp = float(b.get("latency_ms_p99", 0.0))
        hp = float(h.get("latency_ms_p99", 0.0))
        lines.append(
            f"| {backend} | {size} | {_accuracy_marker(br, hr)} {_fmt_delta(br, hr)} "
            f"| {_accuracy_marker(bm, hm)} {_fmt_delta(bm, hm)} "
            f"| {_latency_marker(bp, hp)} {hp:.3f} (base {bp:.3f}) |"
        )
    return "\n".join(lines)


def _render_context_section(base: dict[str, Any], head: dict[str, Any]) -> str:
    """Render the context-pipeline delta table (token + drop counts per scenario)."""
    base_idx = _context_index(base)
    head_idx = _context_index(head)
    keys = sorted(set(base_idx) | set(head_idx))
    if not keys:
        return ""
    lines = [
        "### Context pipeline (per scenario)",
        "",
        "| scenario | tokens | dropped | dedup |",
        "|---|---|---|---|",
    ]
    for scen in keys:
        b = base_idx.get(scen, {})
        h = head_idx.get(scen, b)
        bt = int(b.get("prompt_tokens", 0))
        ht = int(h.get("prompt_tokens", 0))
        bd = int(b.get("items_dropped", 0))
        hd = int(h.get("items_dropped", 0))
        bz = int(b.get("dedup_removed", 0))
        hz = int(h.get("dedup_removed", 0))
        lines.append(
            f"| {scen} | {ht} (base {bt}, Δ{ht - bt:+d}) "
            f"| {hd} (base {bd}, Δ{hd - bd:+d}) "
            f"| {hz} (base {bz}, Δ{hz - bz:+d}) |"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


# A stable HTML marker placed inside the comment body so the CI bot can find
# its previous comment and update it in place (sticky semantics — issue #211
# acceptance criterion "Comment is idempotent").
COMMENT_MARKER = "<!-- contextweaver:benchmark-delta -->"


def render_delta(base: dict[str, Any], head: dict[str, Any]) -> str:
    """Return the full PR-comment markdown body for a base/head pair."""
    parts: list[str] = [
        COMMENT_MARKER,
        "## Benchmark delta (vs `main`)",
        "",
        "Soft regression feedback only — this comment never blocks the PR.",
        f"Latency budget: ⚠️ when `head > base × {LATENCY_BUDGET_MULTIPLIER}`. "
        f"Accuracy budget: ⚠️ when `head < base - {ACCURACY_REGRESSION_PP:.0f}pp`.",
        "",
    ]
    for section in (
        _render_routing_section(base, head),
        _render_matrix_section(base, head),
        _render_context_section(base, head),
    ):
        if section:
            parts.append(section)
            parts.append("")

    parts.extend(
        [
            "---",
            "",
            "Numbers come from `make benchmark` / `make benchmark-matrix`. ",
            "Latency is hardware-dependent — treat the markers as a rough guide.",
            "See `benchmarks/scorecard.md` for the full picture.",
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
    parser.add_argument("--base", required=True, help="Baseline (e.g. main) latest.json")
    parser.add_argument("--head", required=True, help="PR head latest.json")
    parser.add_argument("--output", default="-", help="Output path; '-' for stdout")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        base = json.loads(Path(args.base).read_text(encoding="utf-8"))
        head = json.loads(Path(args.head).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"error: malformed JSON: {exc}", file=sys.stderr)
        return 1
    body = render_delta(base, head)
    if args.output == "-":
        sys.stdout.write(body)
    else:
        Path(args.output).write_text(body, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

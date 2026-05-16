#!/usr/bin/env python3
"""Soft regression PR comment renderer (issue #211).

Compares two ``benchmarks/results/latest.json`` files (head vs base) and
emits a markdown delta table suitable for posting as a sticky comment on
pull requests. The comment is *informational only* — the CI step that
runs this script stays ``continue-on-error: true`` per the issue spec.

The renderer is intentionally stdlib-only so it can run before the
package is installed, matching the ``render_scorecard.py`` convention.

Usage::

    python scripts/benchmark_delta.py --base main.json --head pr.json
    python scripts/benchmark_delta.py --base main.json --head pr.json --output delta.md

Latency-budget convention (Round 2 Q5=C):

    ⚠️  head_p99 > base_p99 × 1.30
    ✅  otherwise

Same threshold the scorecard renderer uses in ``render_scorecard.py``.

Exit codes: 0 on success (delta computed; PR is never gated by this).
``1`` only on argument or I/O error.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

# Keep in sync with scripts/render_scorecard.py::_LATENCY_BUDGET_MULTIPLIER —
# both pieces of CI tooling must agree on the warn threshold.
LATENCY_BUDGET_MULTIPLIER = 1.30

# A stable PR-comment marker so the sticky-comment workflow can find and
# update its own previous comment instead of posting duplicates. Must
# survive Markdown rendering on GitHub — using an HTML comment is safe.
COMMENT_MARKER = "<!-- contextweaver:benchmark-delta -->"


def _delta_cell(base: float | None, head: float | None, fmt: str = "{:.4f}") -> str:
    """Render a single base/head/Δ triple. ``None`` indicates a missing row."""
    if base is None and head is None:
        return "— / — / —"
    if base is None:
        assert head is not None
        return f"— / {fmt.format(head)} / new"
    if head is None:
        return f"{fmt.format(base)} / — / removed"
    delta = head - base
    sign = "+" if delta >= 0 else ""
    return f"{fmt.format(base)} / {fmt.format(head)} / {sign}{fmt.format(delta)}"


def _latency_marker(base_p99: float, head_p99: float) -> str:
    """⚠️ when head exceeds the +30% latency budget; ✅ otherwise.

    A non-positive ``base_p99`` is treated as "no baseline" and always
    returns ✅ — there's no defensible budget to overshoot.
    """
    if base_p99 <= 0:
        return "✅"
    return "⚠️" if head_p99 > base_p99 * LATENCY_BUDGET_MULTIPLIER else "✅"


def _key(row: dict[str, Any]) -> tuple[str, int]:
    """Stable join key over routing and matrix rows."""
    return (str(row.get("backend") or "tfidf"), int(row["catalog_size"]))


def _index(rows: list[dict[str, Any]]) -> dict[tuple[str, int], dict[str, Any]]:
    return {_key(r): r for r in rows}


def _routing_delta_table(
    base_rows: list[dict[str, Any]],
    head_rows: list[dict[str, Any]],
    k: int,
) -> str:
    """Render the legacy single-backend routing delta table."""
    base_idx = _index(base_rows)
    head_idx = _index(head_rows)
    keys = sorted(set(base_idx) | set(head_idx))
    header = (
        f"| backend | catalog | recall@{k} (base / head / Δ) | "
        "MRR (base / head / Δ) | p99 ms (base / head / Δ) | latency |"
    )
    sep = "|---|---:|:---|:---|:---|:---:|"
    lines = [header, sep]
    for key in keys:
        b = base_idx.get(key)
        h = head_idx.get(key)
        backend, size = key
        rec = _delta_cell(
            None if b is None else float(b["recall_at_k"]),
            None if h is None else float(h["recall_at_k"]),
        )
        mrr = _delta_cell(
            None if b is None else float(b["mrr"]),
            None if h is None else float(h["mrr"]),
        )
        p99 = _delta_cell(
            None if b is None else float(b["latency_ms_p99"]),
            None if h is None else float(h["latency_ms_p99"]),
            "{:.3f}",
        )
        marker = "✅"
        if b is not None and h is not None:
            marker = _latency_marker(float(b["latency_ms_p99"]), float(h["latency_ms_p99"]))
        lines.append(f"| {backend} | {size} | {rec} | {mrr} | {p99} | {marker} |")
    return "\n".join(lines)


def _context_delta_table(
    base_rows: list[dict[str, Any]],
    head_rows: list[dict[str, Any]],
) -> str:
    """Render the context-pipeline scenario delta table."""
    base_idx = {str(r["scenario"]): r for r in base_rows}
    head_idx = {str(r["scenario"]): r for r in head_rows}
    scenarios = sorted(set(base_idx) | set(head_idx))
    header = (
        "| scenario | prompt_tokens (base / head / Δ) | "
        "dropped (base / head / Δ) | dedup (base / head / Δ) |"
    )
    sep = "|---|:---|:---|:---|"
    lines = [header, sep]
    for s in scenarios:
        b = base_idx.get(s)
        h = head_idx.get(s)
        tok = _delta_cell(
            None if b is None else float(b["prompt_tokens"]),
            None if h is None else float(h["prompt_tokens"]),
            "{:.0f}",
        )
        dropped = _delta_cell(
            None if b is None else float(b["items_dropped"]),
            None if h is None else float(h["items_dropped"]),
            "{:.0f}",
        )
        dedup = _delta_cell(
            None if b is None else float(b["dedup_removed"]),
            None if h is None else float(h["dedup_removed"]),
            "{:.0f}",
        )
        lines.append(f"| {s} | {tok} | {dropped} | {dedup} |")
    return "\n".join(lines)


def render(base: dict[str, Any], head: dict[str, Any]) -> str:
    """Return the sticky comment markdown for a head/base ``latest.json`` pair."""
    k = int(head.get("k") or base.get("k") or 5)
    parts: list[str] = [
        COMMENT_MARKER,
        "## Benchmark delta (informational, soft gate)",
        "",
        "Each cell shows `base / head / Δ`. The latency column flags cells "
        f"where `head_p99 > base_p99 × {LATENCY_BUDGET_MULTIPLIER:.2f}` "
        "(Round 2 Q5=C). This comment is regenerated on every push; "
        "it does **not** block merging (`continue-on-error: true`).",
        "",
        "### Routing",
        "",
        _routing_delta_table(list(base.get("routing", [])), list(head.get("routing", [])), k),
        "",
    ]

    base_matrix = list(base.get("matrix", []))
    head_matrix = list(head.get("matrix", []))
    if base_matrix or head_matrix:
        # The matrix uses the same row shape as the routing table — but with
        # a non-default backend key — so the same renderer handles both.
        parts += [
            "### Matrix (#208)",
            "",
            _routing_delta_table(base_matrix, head_matrix, k),
            "",
        ]

    parts += [
        "### Context pipeline",
        "",
        _context_delta_table(list(base.get("context", [])), list(head.get("context", []))),
        "",
    ]
    return "\n".join(parts)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else None,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--base", required=True, help="Path to base latest.json (from main)")
    parser.add_argument("--head", required=True, help="Path to head latest.json (from PR)")
    parser.add_argument(
        "--output",
        default="-",
        help="Output markdown path; '-' (default) writes to stdout.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    base_path = Path(args.base)
    head_path = Path(args.head)
    if not head_path.exists():
        print(f"error: --head {head_path} not found", file=sys.stderr)
        return 1
    head = json.loads(head_path.read_text(encoding="utf-8"))
    if base_path.exists():
        base = json.loads(base_path.read_text(encoding="utf-8"))
    else:
        # First-PR-after-baseline-merge case: no base artifact yet.
        # Emit a short "no baseline" comment instead of failing.
        msg = (
            COMMENT_MARKER + "\n## Benchmark delta\n\nNo baseline `latest.json` available on "
            "`main` yet — this comment will populate once the matrix issue lands "
            "and a baseline is committed. (`continue-on-error: true`).\n"
        )
        if args.output == "-":
            sys.stdout.write(msg)
        else:
            Path(args.output).write_text(msg, encoding="utf-8")
        return 0

    rendered = render(base, head)
    if args.output == "-":
        sys.stdout.write(rendered)
    else:
        Path(args.output).write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

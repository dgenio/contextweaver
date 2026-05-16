#!/usr/bin/env python3
"""Render the public benchmark scorecard from ``benchmarks/results/latest.json``.

The renderer reads the JSON dropped by ``benchmarks/benchmark.py`` and emits
``benchmarks/scorecard.md`` — a public, committed, deterministic document so
downstream readers can see contextweaver's routing and context-pipeline
characteristics without running the harness themselves (#197).

Determinism contract: given the same input JSON, the rendered markdown is
byte-identical on every run. CI can verify with::

    make benchmark && make scorecard && git diff --quiet benchmarks/scorecard.md

Usage::

    python scripts/render_scorecard.py
    python scripts/render_scorecard.py --input path.json --output path.md
    python scripts/render_scorecard.py --check    # exits non-zero on drift

The script is intentionally stdlib-only — no contextweaver imports, so it can
run before the package is installed (matching the ``scripts/gen_llms.py``
convention).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = REPO_ROOT / "benchmarks" / "results" / "latest.json"
DEFAULT_OUTPUT = REPO_ROOT / "benchmarks" / "scorecard.md"


# ---------------------------------------------------------------------------
# Rendering primitives
# ---------------------------------------------------------------------------


_REQUIRED_TOP_KEYS = ("benchmark_version", "k", "seed", "routing", "context")
_REQUIRED_ROUTING_KEYS = (
    "catalog_size",
    "queries_evaluated",
    "precision_at_k",
    "recall_at_k",
    "mrr",
    "latency_ms_p50",
    "latency_ms_p95",
    "latency_ms_p99",
)
_REQUIRED_CONTEXT_KEYS = (
    "scenario",
    "event_count",
    "items_included",
    "items_dropped",
    "dedup_removed",
    "prompt_tokens",
    "budget_tokens",
    "budget_utilization_pct",
    "artifacts_created",
    "avg_compaction_ratio",
)
_REQUIRED_MATRIX_KEYS = (
    "backend",
    "catalog_size",
    "queries_evaluated",
    "precision_at_k",
    "recall_at_k",
    "mrr",
    "latency_ms_p50",
    "latency_ms_p95",
    "latency_ms_p99",
)

# Latency-budget marker convention (#210 / #211 / #213 Round 2 Q5=C):
# a cell whose p99 exceeds ``baseline × 1.30`` is flagged with ⚠️; cells
# within budget are flagged with ✅. The baseline for each catalog size
# is the smallest p99 observed across backends in the same matrix run —
# so the marker tracks "is this backend slow at this size?", not
# absolute hardware speed.
_LATENCY_BUDGET_MULTIPLIER = 1.30


def _validate(payload: dict[str, Any]) -> None:
    """Raise ``ValueError`` if *payload* is missing fields the renderer needs."""
    missing = [k for k in _REQUIRED_TOP_KEYS if k not in payload]
    if missing:
        raise ValueError(f"latest.json missing top-level keys: {missing}")
    for row in payload["routing"]:
        miss = [k for k in _REQUIRED_ROUTING_KEYS if k not in row]
        if miss:
            raise ValueError(f"routing row missing keys: {miss}")
    for row in payload["context"]:
        miss = [k for k in _REQUIRED_CONTEXT_KEYS if k not in row]
        if miss:
            raise ValueError(f"context row missing keys: {miss}")
    # Matrix block is optional (back-compat with pre-#208 latest.json); only
    # validate keys when present and the cell is not a skip row.
    for row in payload.get("matrix", []):
        miss = [k for k in _REQUIRED_MATRIX_KEYS if k not in row]
        if miss:
            raise ValueError(f"matrix row missing keys: {miss}")


def _routing_table(rows: list[dict[str, Any]], k: int) -> str:
    header = (
        f"| catalog_size | queries | precision@{k} | recall@{k} | "
        "MRR | p50 (ms) | p95 (ms) | p99 (ms) |"
    )
    sep = "|---:|---:|---:|---:|---:|---:|---:|---:|"
    lines = [header, sep]
    for r in sorted(rows, key=lambda x: int(x["catalog_size"])):
        lines.append(
            "| {size} | {q} | {prec:.4f} | {rec:.4f} | {mrr:.4f} "
            "| {p50:.3f} | {p95:.3f} | {p99:.3f} |".format(
                size=int(r["catalog_size"]),
                q=int(r["queries_evaluated"]),
                prec=float(r["precision_at_k"]),
                rec=float(r["recall_at_k"]),
                mrr=float(r["mrr"]),
                p50=float(r["latency_ms_p50"]),
                p95=float(r["latency_ms_p95"]),
                p99=float(r["latency_ms_p99"]),
            )
        )
    return "\n".join(lines)


def _context_table(rows: list[dict[str, Any]]) -> str:
    header = (
        "| scenario | events | included | dropped | dedup | "
        "tokens | budget | util % | artifacts | compaction |"
    )
    sep = "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
    lines = [header, sep]
    for r in sorted(rows, key=lambda x: str(x["scenario"])):
        lines.append(
            "| {s} | {ev} | {inc} | {dr} | {dd} | {tok} | {bud} "
            "| {util:.1f}% | {art} | {comp:.2f}x |".format(
                s=str(r["scenario"]),
                ev=int(r["event_count"]),
                inc=int(r["items_included"]),
                dr=int(r["items_dropped"]),
                dd=int(r["dedup_removed"]),
                tok=int(r["prompt_tokens"]),
                bud=int(r["budget_tokens"]),
                util=float(r["budget_utilization_pct"]),
                art=int(r["artifacts_created"]),
                comp=float(r["avg_compaction_ratio"]),
            )
        )
    return "\n".join(lines)


def _latency_baseline(cells: list[dict[str, Any]]) -> dict[int, float]:
    """Return ``{catalog_size: min_p99}`` for cells that actually ran.

    The minimum p99 across backends at each size is the "fastest backend at
    this scale" baseline; ⚠️ markers fire on cells slower than
    ``baseline × _LATENCY_BUDGET_MULTIPLIER``.
    """
    baseline: dict[int, float] = {}
    for c in cells:
        if c.get("status"):
            continue
        size = int(c["catalog_size"])
        p99 = float(c["latency_ms_p99"])
        if size not in baseline or p99 < baseline[size]:
            baseline[size] = p99
    return baseline


def _matrix_table(cells: list[dict[str, Any]], k: int) -> str:
    """Render the per-backend × per-size matrix table with ✅/⚠️ markers."""
    header = (
        f"| backend | catalog_size | queries | recall@{k} | MRR | "
        "p50 (ms) | p95 (ms) | p99 (ms) | latency |"
    )
    sep = "|---|---:|---:|---:|---:|---:|---:|---:|:---:|"
    lines = [header, sep]
    baseline = _latency_baseline(cells)
    ordered = sorted(cells, key=lambda c: (str(c["backend"]), int(c["catalog_size"])))
    for c in ordered:
        status = str(c.get("status") or "")
        if status:
            lines.append(
                "| {b} | {sz} | — | — | — | — | — | — | _{st}_ |".format(
                    b=str(c["backend"]), sz=int(c["catalog_size"]), st=status
                )
            )
            continue
        size = int(c["catalog_size"])
        p99 = float(c["latency_ms_p99"])
        base = baseline.get(size, p99)
        marker = "⚠️" if p99 > base * _LATENCY_BUDGET_MULTIPLIER else "✅"
        lines.append(
            "| {b} | {sz} | {q} | {rec:.4f} | {mrr:.4f} "
            "| {p50:.3f} | {p95:.3f} | {p99:.3f} | {m} |".format(
                b=str(c["backend"]),
                sz=size,
                q=int(c["queries_evaluated"]),
                rec=float(c["recall_at_k"]),
                mrr=float(c["mrr"]),
                p50=float(c["latency_ms_p50"]),
                p95=float(c["latency_ms_p95"]),
                p99=p99,
                m=marker,
            )
        )
    return "\n".join(lines)


def _per_namespace_table(per_ns: dict[str, dict[str, float]], k: int) -> str:
    """Render the per-namespace recall@k table across backends."""
    if not per_ns:
        return "_No per-namespace data in this run._"
    backends = sorted(per_ns.keys())
    namespaces = sorted({ns for table in per_ns.values() for ns in table})
    header_cells = ["| namespace |"] + [f" recall@{k} ({b}) |" for b in backends]
    header = "".join(header_cells)
    sep = "|---|" + "---:|" * len(backends)
    lines = [header, sep]
    for ns in namespaces:
        row = [f"| {ns} |"]
        for b in backends:
            v = per_ns.get(b, {}).get(ns)
            row.append(" — |" if v is None else f" {float(v):.4f} |")
        lines.append("".join(row))
    return "\n".join(lines)


def _naive_table(context_rows: list[dict[str, Any]]) -> str:
    """Render the vs-naïve-concat section; rows missing ``naive_delta`` are skipped."""
    header = "| scenario | naive_tokens | cw_tokens | pct_reduction | coverage % |"
    sep = "|---|---:|---:|---:|---:|"
    lines = [header, sep]
    for r in sorted(context_rows, key=lambda x: str(x["scenario"])):
        delta = r.get("naive_delta")
        if not delta:
            continue
        lines.append(
            "| {s} | {nt} | {ct} | {pct:.2f}% | {cov:.2f}% |".format(
                s=str(r["scenario"]),
                nt=int(float(delta["naive_tokens"])),
                ct=int(float(delta["cw_tokens"])),
                pct=float(delta["pct_reduction"]),
                cov=float(delta["coverage_pct"]),
            )
        )
    if len(lines) == 2:
        return (
            "_No naïve-baseline data in this run (regenerate with the default `make benchmark`)._"
        )
    return "\n".join(lines)


def _measured_pct_reduction(context_rows: list[dict[str, Any]]) -> float | None:
    """Mean ``pct_reduction`` across scenarios that have a naïve_delta block."""
    values = [
        float(r["naive_delta"]["pct_reduction"]) for r in context_rows if r.get("naive_delta")
    ]
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def render(payload: dict[str, Any]) -> str:
    """Return the scorecard markdown for *payload* (deterministic)."""
    _validate(payload)
    k = int(payload["k"])
    seed = int(payload["seed"])
    benchmark_version = str(payload["benchmark_version"])
    # Derive the answer-phase budget from the context rows so the scorecard
    # narrative tracks the harness rather than drifting against a hard-coded
    # literal. All scenarios run under the same budget; assert that to make
    # the invariant explicit.
    context_rows = list(payload["context"])
    budgets = {int(r["budget_tokens"]) for r in context_rows} if context_rows else {0}
    if len(budgets) != 1:
        raise ValueError(f"context rows must share a single budget_tokens; got {sorted(budgets)}")
    answer_budget = next(iter(budgets))

    matrix_rows = list(payload.get("matrix", []))
    per_namespace = dict(payload.get("per_namespace", {}))
    measured_pct = _measured_pct_reduction(context_rows)

    # Derive the active gold-set size from any matrix cell's queries_evaluated
    # (max across cells, since smaller catalogs may filter some queries out).
    # Falls back to the legacy single-backend rows when no matrix is present.
    gold_size = 0
    for row in matrix_rows + payload["routing"]:
        gold_size = max(gold_size, int(row.get("queries_evaluated", 0)))

    parts = [
        "# contextweaver — Benchmark Scorecard",
        "",
        "> Auto-generated by `make scorecard`. Do not edit by hand.",
        "> Source: `benchmarks/results/latest.json` (produced by `make benchmark`).",
        "",
        f"- Harness version: `{benchmark_version}`",
        f"- Seed: `{seed}`",
        f"- Rank cutoff `k`: `{k}`",
        "- Token estimator: `CharDivFourEstimator` (deterministic, no model dependency)",
        f"- Answer-phase budget: `{answer_budget}` tokens",
        f"- Gold dataset: `{gold_size}` queries across 8 catalog namespaces",
        "",
        "All numbers below are reproducible deterministically by running",
        "`make benchmark && make scorecard` from a clean checkout. Hardware and",
        "Python version affect latency only; recall, drops, dedup, and token",
        "counts are environment-independent.",
        "",
        "---",
        "",
        "## Routing accuracy & latency",
        "",
        "Single-backend (TF-IDF) summary at the legacy catalog sizes "
        "(50, ~83 natural cap, 1000-item synthetic extension). Each query is "
        "repeated three times for latency percentile stability.",
        "",
        _routing_table(payload["routing"], k),
        "",
        "Reading the table:",
        "",
        f"- `precision@{k}` is bounded by `1 / k` when each query has a single",
        "  expected tool, so the headline accuracy signal is `recall@k` and `MRR`.",
        "- Recall degrades predictably as the catalog grows — noise items",
        "  compete with true matches; the routing-only experience for catalogs",
        "  larger than ~200 items benefits from one of the optional retrieval",
        "  backends (`bm25`, `fuzzy`) configured via `Router(scorer_backend=...)`.",
        "- p50/p95 latencies stay under a millisecond at catalog_size ≤ 83.",
        "  At catalog_size 1000 the p99 climbs into the tens of milliseconds",
        "  because the beam search has to evaluate substantially more children",
        "  per step; this is the regime where switching to a retriever-first",
        "  shortlist (the `Retriever` protocol on the `EngineRegistry`) is the",
        "  expected next step.",
        "",
    ]

    if matrix_rows:
        parts += [
            "---",
            "",
            "## Per-backend × per-size matrix (#208)",
            "",
            "Sweeps the three available scorer backends across catalog sizes "
            "100 / 500 / 1000 from the expanded 200-query gold set. The "
            "`latency` column flags cells whose p99 exceeds `min_p99 × "
            f"{_LATENCY_BUDGET_MULTIPLIER:.2f}` at the same catalog size — "
            'a portable "is this backend slow at this scale?" marker that '
            "does not depend on absolute hardware speed.",
            "",
            _matrix_table(matrix_rows, k),
            "",
            "Reading the table:",
            "",
            "- ✅ — cell p99 within `min_p99 × 1.30` at the same size.",
            "- ⚠️ — cell p99 exceeds the budget; investigate before merging.",
            "- Skipped rows (e.g., `fuzzy` without `[retrieval]`) carry an",
            "  italic status note instead of metrics; they never silently drop.",
            "",
        ]

    if per_namespace:
        parts += [
            "---",
            "",
            "## Per-namespace recall (#209)",
            "",
            "Recall@k broken down by namespace at the largest matrix size — "
            "the regime where backend differences are most observable. Each "
            "namespace is represented by ≥20 hand-authored queries.",
            "",
            _per_namespace_table(per_namespace, k),
            "",
        ]

    parts += [
        "---",
        "",
        "## Context pipeline scenarios",
        "",
        "Reference event logs under `benchmarks/scenarios/` are pushed through",
        "`ContextManager.build_sync(phase=Phase.answer)`. The firewall",
        "intercepts every `tool_result`; large results become artifacts and the",
        "prompt sees their summaries instead.",
        "",
        _context_table(payload["context"]),
        "",
        "Reading the table:",
        "",
        "- `dropped > 0` means `select_and_pack` had to evict candidates to",
        "  stay under the answer-phase budget — the `stress_conversation`",
        "  scenario is sized to force this so the budget-driven selection",
        "  stage shows up in benchmark output (#181).",
        "- `dedup > 0` proves the Jaccard near-duplicate stage actively",
        "  removed redundant context.",
        "- `compaction > 1.0×` is the average ratio of raw artifact bytes to",
        "  injected summary bytes — that's the firewall's load-bearing job.",
        "",
    ]

    if any(r.get("naive_delta") for r in context_rows):
        parts += [
            "---",
            "",
            "## vs naïve concat (#215)",
            "",
            "`naive_tokens` is the token count of \"concatenate every event's "
            'text" — what an integrator would get without contextweaver. '
            "`pct_reduction` is the saving over that baseline; `coverage %` is "
            "the fraction of parent-id chains preserved in the rendered "
            "prompt (parent text appears verbatim in the prompt for at least "
            "its first 40 characters).",
            "",
            _naive_table(context_rows),
            "",
        ]
        if measured_pct is not None:
            parts += [
                f"**Average reduction across scenarios: `{measured_pct:.2f}%`** "
                "(measured, not illustrative). Light-load scenarios may show "
                "0% reduction because the input is already smaller than "
                "contextweaver's render overhead; the stress scenario is the "
                "regime where the firewall's load-bearing work shows up.",
                "",
            ]

    parts += [
        "---",
        "",
        "## Methodology",
        "",
        "- **Deterministic seeds.** All catalog generation, scenario loading,",
        "  and beam-search tie-breaking is seeded; identical inputs always",
        "  produce identical outputs (`make benchmark` is a no-op on a fresh",
        "  re-run for routing accuracy + context metrics; only latency",
        "  varies with hardware).",
        "- **No LLM calls.** The harness is pure-Python, stdlib + minimal core",
        "  deps. The token estimator is `CharDivFourEstimator` so the numbers",
        "  do not depend on `tiktoken`'s cached encoding state.",
        "- **No network access.** The benchmark is safe to run in air-gapped",
        "  CI environments.",
        "- **Hardware variance.** Latency numbers are measured on the runner",
        "  that produced `latest.json`. Treat them as ordering, not absolutes:",
        "  the relative cost between catalog sizes is portable; the absolute",
        "  microsecond count is not.",
        "",
        "See [`benchmarks/README.md`](README.md) for the full harness reference",
        "and the per-scenario notes.",
        "",
        "---",
        "",
        "## Regenerating",
        "",
        "```bash",
        "make benchmark   # writes benchmarks/results/latest.json",
        "make scorecard   # writes benchmarks/scorecard.md from latest.json",
        "git diff --quiet benchmarks/scorecard.md   # passes on clean re-run",
        "```",
        "",
        "Weekly scheduled regeneration runs via `.github/workflows/"
        "scorecard-weekly.yml`; per-PR regression deltas via",
        "`.github/workflows/benchmark-delta.yml` post a sticky comment.",
        "",
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else None,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Path to latest.json")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Path to scorecard.md")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write; exit non-zero if the existing file would change.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Render the scorecard. Returns 0 on success, 1 on drift in --check mode."""
    args = _parse_args(argv)
    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(
            f"error: {input_path} not found — run `make benchmark` first.",
            file=sys.stderr,
        )
        return 1

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    rendered = render(payload)

    if args.check:
        existing = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
        if existing != rendered:
            print(
                f"error: {output_path} is out of date. Run `make scorecard`.",
                file=sys.stderr,
            )
            return 1
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

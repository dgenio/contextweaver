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


def _matrix_table(cells: list[dict[str, Any]], k: int) -> str:
    """Render the per-backend × per-size matrix (#208).

    Cells with a non-``ok`` ``status`` (e.g. ``"skipped: missing rapidfuzz"``)
    are shown with metric placeholders so per-backend coverage is never
    silently omitted.
    """
    header = (
        f"| backend | catalog_size | queries | precision@{k} | recall@{k} | "
        "MRR | p50 (ms) | p95 (ms) | p99 (ms) | status |"
    )
    sep = "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"
    lines = [header, sep]
    for r in sorted(
        cells, key=lambda x: (str(x.get("backend", "")), int(x.get("catalog_size", 0)))
    ):
        status = str(r.get("status", "ok"))
        if status != "ok":
            lines.append(
                "| {b} | {sz} | — | — | — | — | — | — | — | {s} |".format(
                    b=str(r["backend"]),
                    sz=int(r["catalog_size"]),
                    s=status,
                )
            )
            continue
        lines.append(
            "| {b} | {sz} | {q} | {prec:.4f} | {rec:.4f} | {mrr:.4f} "
            "| {p50:.3f} | {p95:.3f} | {p99:.3f} | ok |".format(
                b=str(r["backend"]),
                sz=int(r["catalog_size"]),
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


def _mixed_shape_matrix_table(cells: list[dict[str, Any]], k: int) -> str:
    """Render the head-heavy + long-tail mixed-namespace matrix (#277).

    Same shape as :func:`_matrix_table` but only emitted when the harness
    was run with ``--mixed-shapes``.  The header is identical so the table
    composes cleanly inside a "Mixed-namespace catalog (500 tools)" section.
    """
    return _matrix_table(cells, k)


def _hardware_section(
    environment: dict[str, Any] | None, reference_rig: dict[str, Any] | None
) -> str:
    """Render the hardware reference rig + measured-on disclosure (#267).

    Returns an empty string when both inputs are missing so the section is
    omitted on legacy ``latest.json`` payloads.
    """
    if not environment and not reference_rig:
        return ""
    lines: list[str] = []
    if reference_rig:
        label = str(reference_rig.get("label", "unspecified"))
        lines.extend(
            [
                "**Reference rig** (numbers in this scorecard are calibrated for):",
                "",
                f"- {label}",
                f"- System: `{reference_rig.get('system', 'unknown')}`",
                f"- Machine: `{reference_rig.get('machine', 'unknown')}`",
                f"- CPU logical cores: `{reference_rig.get('cpu_logical_cores', 'unknown')}`",
                f"- Python: `{reference_rig.get('python_version', 'unknown')}`",
                "",
                f"> {reference_rig.get('notes', '')}",
                "",
            ]
        )
    if environment:
        lines.extend(
            [
                "**Measured on** (host that produced the current `latest.json`):",
                "",
                f"- System: `{environment.get('system', 'unknown')}`",
                f"- Machine: `{environment.get('machine', 'unknown')}`",
                f"- Processor: `{environment.get('processor', 'unknown')}`",
                f"- CPU logical cores: `{environment.get('cpu_logical_cores', 'unknown')}`",
                f"- Python: `{environment.get('python_implementation', 'unknown')} "
                f"{environment.get('python_version', 'unknown')}`",
                f"- Platform: `{environment.get('platform_string', 'unknown')}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def _tiktoken_parity_section(parity: dict[str, Any] | None) -> str:
    """Render the ``CharDivFourEstimator`` vs ``cl100k_base`` parity section (#268).

    Returns an empty string when ``parity`` is missing.  When ``status`` is
    not ``ok`` (e.g. tiktoken offline) the section emits the status row so
    readers can see why the parity figures are absent.
    """
    if not parity:
        return ""
    status = str(parity.get("status", "ok"))
    if status != "ok":
        return (
            "_Token-estimator parity check is unavailable on this run "
            f"(`status: {status}`); the headline `CharDivFourEstimator` numbers "
            "elsewhere in this scorecard are still valid — they do not depend on "
            "`tiktoken` at runtime._"
        )
    return "\n".join(
        [
            "| metric | value |",
            "|---|---:|",
            f"| samples | {int(parity.get('samples', 0))} |",
            f"| mean abs error (tokens) | {float(parity.get('mean_abs_error', 0.0)):.4f} |",
            f"| max abs error (tokens) | {int(parity.get('max_abs_error', 0))} |",
            "| mean signed error (cw − tiktoken) | "
            f"{float(parity.get('mean_signed_error', 0.0)):+.4f} |",
            f"| mean ratio (cw ÷ tiktoken) | {float(parity.get('mean_ratio', 0.0)):.4f} |",
        ]
    )


def _e2e_real_model_section(e2e: dict[str, Any] | None) -> str:
    """Render the optional end-to-end real-model section (#269).

    The section is **always** emitted with at minimum a "how to enable"
    block so the scorecard documents the opt-in even when E2E was off.
    """
    if not e2e:
        return ""
    status = str(e2e.get("status", "skipped: offline by default"))
    if status != "ok":
        note = str(e2e.get("note", ""))
        lines = [f"_Status: `{status}`._", ""]
        if note:
            lines.append(f"> {note}")
        return "\n".join(lines)
    provider = str(e2e.get("provider", "unknown"))
    model = str(e2e.get("model", "unknown"))
    samples = int(e2e.get("samples", 0))
    pt = int(e2e.get("prompt_tokens_total", 0))
    ct = int(e2e.get("completion_tokens_total", 0))
    usd = float(e2e.get("estimated_usd_cost", 0.0))
    p50 = float(e2e.get("e2e_latency_ms_p50", 0.0))
    p95 = float(e2e.get("e2e_latency_ms_p95", 0.0))
    p99 = float(e2e.get("e2e_latency_ms_p99", 0.0))
    return "\n".join(
        [
            f"- Provider: `{provider}` · Model: `{model}` · Samples: `{samples}`",
            f"- Prompt tokens (total): `{pt}` · Completion tokens (total): `{ct}`",
            f"- Estimated cost (USD): `${usd:.4f}` "
            f"({'unpriced' if usd == 0.0 else 'priced from harness rate table'})",
            f"- E2E latency: p50 `{p50:.2f} ms` · p95 `{p95:.2f} ms` · p99 `{p99:.2f} ms`",
        ]
    )


def _per_namespace_table(rows: list[dict[str, Any]], k: int) -> str:
    """Render the per-namespace recall@k breakdown (#209)."""
    header = f"| backend | catalog_size | namespace | queries | recall@{k} |"
    sep = "|---|---:|---|---:|---:|"
    lines = [header, sep]
    for r in sorted(
        rows,
        key=lambda x: (
            str(x.get("backend", "")),
            int(x.get("catalog_size", 0)),
            str(x.get("namespace", "")),
        ),
    ):
        lines.append(
            "| {b} | {sz} | {ns} | {q} | {rec:.4f} |".format(
                b=str(r["backend"]),
                sz=int(r["catalog_size"]),
                ns=str(r["namespace"]),
                q=int(r["queries_evaluated"]),
                rec=float(r["recall_at_k"]),
            )
        )
    return "\n".join(lines)


def _naive_delta_table(context_rows: list[dict[str, Any]]) -> str:
    """Render the naïve-concat baseline section (#215) when rows carry ``naive_delta``.

    Returns an empty string when no row has a ``naive_delta`` block — the
    caller is expected to gate emission accordingly.
    """
    annotated = [r for r in context_rows if isinstance(r.get("naive_delta"), dict)]
    if not annotated:
        return ""
    header = "| scenario | naive_tokens | cw_tokens | token reduction | coverage proxy |"
    sep = "|---|---:|---:|---:|---:|"
    lines = [header, sep]
    for r in sorted(annotated, key=lambda x: str(x["scenario"])):
        nd = r["naive_delta"]
        lines.append(
            "| {s} | {nt} | {ct} | {pct:.2f}% | {cov:.2f}% |".format(
                s=str(r["scenario"]),
                nt=int(nd.get("naive_tokens", 0)),
                ct=int(nd.get("cw_tokens", 0)),
                pct=float(nd.get("pct_reduction", 0.0)),
                cov=float(nd.get("coverage_pct", 0.0)),
            )
        )
    return "\n".join(lines)


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
        "Gold dataset: 200 hand-curated queries (`benchmarks/routing_gold.json`)",
        "covering all 8 catalog namespaces. Each query is repeated three times",
        "for latency percentile stability.",
        "",
        _routing_table(payload["routing"], k),
        "",
    ]

    # Per-backend × per-size matrix (issue #208) — additive, gated by presence.
    matrix_rows = payload.get("routing_matrix")
    if isinstance(matrix_rows, list) and matrix_rows:
        parts.extend(
            [
                "### Per-backend × per-size matrix",
                "",
                "Generated by `make benchmark-matrix` (issue #208). The matrix lets",
                "you compare the bundled `tfidf` baseline against the optional",
                "`bm25` (core dep), `fuzzy` (`[retrieval]` extra), and embedding",
                "backends (`embedding_hashing` stdlib baseline, `embedding_st`",
                "`[embeddings]` extra — #266) across three catalog sizes.",
                "Skipped backends are recorded explicitly.",
                "",
                _matrix_table(matrix_rows, k),
                "",
            ]
        )

    # Mixed-namespace head-heavy + long-tail matrix (issue #277).
    mixed_shape_rows = payload.get("routing_matrix_mixed_shape")
    if isinstance(mixed_shape_rows, list) and mixed_shape_rows:
        parts.extend(
            [
                "### Mixed-namespace catalog (500 tools, head + long tail)",
                "",
                "Generated by `python benchmarks/benchmark.py --matrix --mixed-shapes`",
                "(issue #277). The mixed-shape catalog has one head namespace",
                "(`analytics_xl`, 200 items), two mid-weight namespaces, four small",
                "operational namespaces, and a 100-namespace long tail (one item",
                "each) — a deliberately asymmetric distribution that contrasts",
                "with the uniform 8-namespace pool used by the headline matrix.",
                "Numbers here are intentionally lower: gold queries only cover",
                "the natural 8 namespaces, so the synthetic head-and-tail tools",
                "act as targeted noise items.",
                "",
                _mixed_shape_matrix_table(mixed_shape_rows, k),
                "",
            ]
        )

    # Per-namespace recall (#209).
    per_ns_rows = payload.get("routing_per_namespace")
    if isinstance(per_ns_rows, list) and per_ns_rows:
        parts.extend(
            [
                "### Per-namespace recall@k",
                "",
                "Breakdown of recall@k by gold-set namespace (issue #209). Useful",
                "for identifying which tool namespaces benefit most from a given",
                "scoring backend.",
                "",
                _per_namespace_table(per_ns_rows, k),
                "",
            ]
        )

    parts.extend(
        [
            "Reading the routing tables:",
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
            "- `compaction == 1.00×` rows (`tiny_payload`, `short_conversation`,",
            "  `large_catalog`) are the firewall correctly no-op'ing on tiny",
            "  inputs — every `tool_result` still passes through the envelope,",
            "  but the summary equals the raw text so no token cost is saved",
            "  *or* added (#271). The headline reduction figures further down",
            "  reflect the firewall's value when payloads are actually large;",
            "  small-payload rows guard against the impression that the",
            "  firewall always wins.",
            "",
        ]
    )

    # Naïve-baseline section (#215).
    naive_block = _naive_delta_table(payload["context"])
    if naive_block:
        parts.extend(
            [
                "### vs. naïve concat baseline",
                "",
                "Token cost and coverage proxy against the no-op baseline of",
                "concatenating *all* tool schemas + the full conversation history.",
                "The naïve token count is measured with `tiktoken.cl100k_base`; the",
                "coverage proxy is `items_included / event_count` (deterministic,",
                "no LLM judge — see `scripts/baseline_naive.py`).",
                "",
                naive_block,
                "",
            ]
        )

    parts.extend(
        [
            "---",
            "",
            "## Methodology",
            "",
            "- **Deterministic seeds.** All catalog generation, scenario loading,",
            "  and beam-search tie-breaking is seeded; identical inputs always",
            "  produce identical outputs (`make benchmark` is a no-op on a fresh",
            "  re-run for routing accuracy + context metrics; only latency",
            "  varies with hardware).",
            "- **No LLM calls by default.** The harness is pure-Python, stdlib +",
            "  minimal core deps. The token estimator is `CharDivFourEstimator`",
            "  so the headline numbers do not depend on `tiktoken`'s cached",
            "  encoding state. The optional end-to-end real-model section (#269)",
            "  is off by default and never runs in CI.",
            "- **No network access by default.** The benchmark is safe to run",
            "  in air-gapped CI environments. The end-to-end real-model capture",
            "  (#269) and the `tiktoken` parity check (#268) require network or",
            "  a cached encoding respectively, and degrade to a `skipped` row",
            "  when unavailable.",
            "- **Hardware variance.** Latency numbers are measured on the runner",
            "  that produced `latest.json`. Treat them as ordering, not absolutes:",
            "  the relative cost between catalog sizes is portable; the absolute",
            "  microsecond count is not. See the **Hardware reference rig**",
            "  section below for the canonical reference machine (#267).",
            "",
        ]
    )
    hw_block = _hardware_section(
        payload.get("environment") if isinstance(payload.get("environment"), dict) else None,
        payload.get("reference_rig") if isinstance(payload.get("reference_rig"), dict) else None,
    )
    if hw_block:
        parts.extend(
            [
                "### Hardware reference rig",
                "",
                hw_block,
                "",
            ]
        )
    parts.extend(
        [
            "### Token-estimator parity check",
            "",
            "Quantifies the drift between `CharDivFourEstimator` (used everywhere",
            "above) and `tiktoken.cl100k_base` on the gold-query corpus. This is",
            "**measurement of the estimator**, not a routing metric — when the",
            "two diverge, the headline token figures above are still self-",
            "consistent, but readers comparing the numbers against an OpenAI",
            "tokenizer expectation should apply the drift below (#268).",
            "",
            _tiktoken_parity_section(
                payload.get("tiktoken_parity")
                if isinstance(payload.get("tiktoken_parity"), dict)
                else None
            ),
            "",
            "### Optional end-to-end real-model capture",
            "",
            "Off by default. When enabled (`--with-real-model` flag plus",
            "`CW_BENCH_LLM_PROVIDER` + `CW_BENCH_LLM_API_KEY` env vars) the",
            "harness sends ≤5 gold queries through an OpenAI-compatible chat",
            "endpoint and records prompt/completion tokens, USD cost, and",
            "round-trip latency. Pure end-to-end, no fan-out, no retries; the",
            "section exists so deployment-cost readers can sanity-check the",
            "harness's offline token estimates against real provider usage (#269).",
            "",
            _e2e_real_model_section(
                payload.get("e2e_real_model")
                if isinstance(payload.get("e2e_real_model"), dict)
                else None
            ),
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
            "Per-backend matrices are generated via `make benchmark-matrix` (#208)",
            "and now cover `tfidf`, `bm25`, `fuzzy`, `embedding_hashing`, and",
            "`embedding_st` (#266). Weekly scheduled regeneration runs out of",
            "`.github/workflows/scorecard-weekly.yml` (#207).",
            "",
            "Optional capture flags:",
            "",
            "- `--mixed-shapes` — emit the head-heavy + long-tail catalog matrix (#277).",
            "- `--no-tiktoken-parity` — disable the estimator parity check (#268).",
            "- `--with-real-model` — run the end-to-end real-model capture (#269).",
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
            # Show a brief diff hint so CI output is actionable.
            old_lines = existing.splitlines()
            new_lines = rendered.splitlines()
            diff_count = sum(1 for a, b in zip(old_lines, new_lines, strict=False) if a != b)
            diff_count += abs(len(old_lines) - len(new_lines))
            print(
                f"error: {output_path} is out of date ({diff_count} lines differ). "
                f"Run `make scorecard`.",
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

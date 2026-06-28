"""Large-catalog routing benchmark: 300+ tools across many namespaces (issue #369).

The headline adoption case is a coding-agent setup with many MCP servers and
hundreds of tools. This deterministic, offline benchmark simulates that shape —
300+ tools across 8 namespaces, with near-duplicate *distractor* variants and
*destructive* (write/side-effecting) tools — and measures whether routing keeps
the right tool reachable while collapsing the prompt:

- recall@1/3/5 and MRR for expected-tool selection (`tool_browse`);
- prompt-token reduction of bounded ``ChoiceCard``s vs the naive all-tools prompt;
- allow/deny filtering of destructive tools (none reach the shortlist when denied).

It reuses the installed package only (no import from sibling benchmark scripts),
mirroring ``benchmarks/smoke_eval.py``. Accuracy and token figures use
``CharDivFourEstimator`` so they are environment-independent; only latency varies
with hardware and is reported to stdout / JSON, never to the committed scorecard.

Usage::

    python benchmarks/large_catalog.py            # write JSON + scorecard
    python benchmarks/large_catalog.py --check     # exit non-zero on scorecard drift
    python benchmarks/large_catalog.py --strict    # exit non-zero if below thresholds

Exit codes: 0 on success; 1 on drift (``--check``) or threshold breach (``--strict``).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from benchmarks._firewall_measurement import measure_firewall  # noqa: E402
from contextweaver.eval.dataset import EvalCase, EvalDataset  # noqa: E402
from contextweaver.eval.routing import evaluate_routing  # noqa: E402
from contextweaver.protocols import CharDivFourEstimator  # noqa: E402
from contextweaver.routing.cards import make_choice_cards, render_cards_text  # noqa: E402
from contextweaver.routing.catalog import (  # noqa: E402
    generate_sample_catalog,
    load_catalog_dicts,
)
from contextweaver.routing.router import Router  # noqa: E402
from contextweaver.routing.tree import TreeBuilder  # noqa: E402
from contextweaver.types import SelectableItem  # noqa: E402

DEFAULT_CATALOG_SIZE = 320
DEFAULT_SEED = 42
TOP_K = 5
BEAM_WIDTH = 3

DEFAULT_JSON = _ROOT / "benchmarks" / "results" / "large_catalog.json"
DEFAULT_SCORECARD = _ROOT / "benchmarks" / "large_catalog_scorecard.md"

# Warn/gate thresholds (issue #369 acceptance: "fails or warns when ... regress").
# These are *regression guards*, set below the deterministic baseline (recall@5
# ≈ 0.71 against the distractor-heavy catalog, token reduction ≈ 97%) with margin
# so a real quality drop trips the warning while the synthetic near-duplicate
# variants deliberately competing for rank do not.
RECALL_AT_5_FLOOR = 0.65
TOKEN_REDUCTION_FLOOR_PCT = 80.0

_EST = CharDivFourEstimator()


def _count(text: str) -> int:
    return _EST.estimate(text)


# ---------------------------------------------------------------------------
# Catalog construction
# ---------------------------------------------------------------------------


def build_large_catalog(n: int, seed: int) -> list[SelectableItem]:
    """Return *n* deterministic tools, extending the 83-item pool with variants.

    Synthetic variants share their original's namespace and tags (preserving
    routing signal density) but carry distinct IDs, so they act as near-duplicate
    *distractors* without ever matching a gold query. Variants are always
    non-destructive (``side_effects`` defaults to ``False`` and is not copied
    from the original), so ``destructive_tools`` in the result reflects the base
    83-item pool only — the deny test exercises exactly that base set.
    """
    base = load_catalog_dicts(generate_sample_catalog(n=83, seed=seed))
    items: list[SelectableItem] = list(base)
    version = 2
    while len(items) < n:
        for orig in list(base):
            items.append(
                SelectableItem(
                    f"{orig.id}.v{version}",
                    orig.kind,
                    f"{orig.name}_v{version}",
                    f"{orig.description} (variant {version})",
                    tags=orig.tags,
                    namespace=orig.namespace,
                )
            )
            if len(items) >= n:
                break
        version += 1
    return sorted(items, key=lambda i: i.id)[:n]


def _gold_dataset(base_items: list[SelectableItem]) -> EvalDataset:
    """Derive a deterministic gold set: each base tool's description -> its id."""
    cases = [EvalCase(query=it.description, expected=[it.id]) for it in base_items]
    return EvalDataset(cases=sorted(cases, key=lambda c: c.query))


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


@dataclass
class LargeCatalogResult:
    """Deterministic + latency results of one large-catalog run."""

    catalog_size: int
    namespaces: int
    distractor_tools: int
    destructive_tools: int
    queries: int
    recall_at_1: float
    recall_at_3: float
    recall_at_5: float
    mrr: float
    mean_naive_tokens: int
    mean_card_tokens: int
    naive_prompt_chars: int
    mean_card_chars: int
    token_reduction_pct: float
    destructive_in_shortlist_denied: int
    namespace_filtered_recall_at_5: float
    namespace_filter_leaks: int
    firewall_raw_chars: int
    firewall_summary_chars: int
    firewall_reduction_pct: float
    firewall_artifact_created: bool
    tool_view_recovered: bool
    raw_result_exposed_inline: bool
    latency_ms_p50: float
    latency_ms_p99: float


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[idx]


def run_benchmark(n: int = DEFAULT_CATALOG_SIZE, seed: int = DEFAULT_SEED) -> LargeCatalogResult:
    """Run the large-catalog benchmark and return its results."""
    items = build_large_catalog(n, seed)
    base_items = [it for it in items if ".v" not in it.id]
    distractors = len(items) - len(base_items)
    destructive = [it for it in items if it.side_effects]
    namespaces = {it.namespace for it in items if it.namespace}

    router = Router(TreeBuilder().build(items), items=items, top_k=TOP_K, beam_width=BEAM_WIDTH)
    catalog_ids = {it.id for it in items}
    dataset = _gold_dataset(base_items)
    report = evaluate_routing(router, dataset, catalog_ids=catalog_ids)

    # Naive prompt = every tool's name + description. Stable across queries.
    naive_text = "\n".join(f"{it.name}: {it.description}" for it in items)
    naive_tokens = _count(naive_text)
    naive_chars = len(naive_text)

    card_token_samples: list[int] = []
    card_char_samples: list[int] = []
    latencies: list[float] = []
    for case in dataset:
        start = time.perf_counter()
        result = router.route(case.query)
        latencies.append((time.perf_counter() - start) * 1000.0)
        cards = make_choice_cards(result.candidate_items)
        rendered_cards = render_cards_text(cards)
        card_token_samples.append(_count(rendered_cards))
        card_char_samples.append(len(rendered_cards))
    mean_card = (
        round(sum(card_token_samples) / len(card_token_samples)) if card_token_samples else 0
    )
    reduction = round((1 - mean_card / naive_tokens) * 100.0, 2) if naive_tokens else 0.0
    mean_card_chars = (
        round(sum(card_char_samples) / len(card_char_samples)) if card_char_samples else 0
    )

    # Namespace gating should retain the expected tool while excluding every
    # candidate from other namespaces.
    item_by_id = {item.id: item for item in items}
    namespace_hits = 0
    namespace_queries = 0
    namespace_leaks = 0
    for case in dataset:
        expected_id = case.expected[0]
        expected_item = item_by_id.get(expected_id)
        if expected_item is None or not expected_item.namespace:
            continue
        gated = router.route(case.query, allowed_namespaces={expected_item.namespace})
        namespace_queries += 1
        namespace_hits += int(expected_id in gated.candidate_ids)
        namespace_leaks += sum(
            item.namespace != expected_item.namespace for item in gated.candidate_items
        )
    namespace_recall = round(namespace_hits / namespace_queries, 4) if namespace_queries else 0.0

    # Exercise the result firewall and the artifact-view path over one large
    # deterministic tool result from the benchmarked catalog.
    raw_result = "invoice_id,status,amount\n" + "\n".join(
        f"INV-{index:04d},unpaid,{index}.00" for index in range(500)
    )
    firewall = measure_firewall(raw_result, tool_name="billing.invoices.search")
    # Allow/deny filtering: deny every destructive tool and confirm none survive.
    deny_ids = {it.id for it in destructive}
    leaked = 0
    if deny_ids:
        for case in dataset:
            shortlist = set(router.route(case.query, exclude_ids=deny_ids).candidate_ids)
            leaked += len(shortlist & deny_ids)

    return LargeCatalogResult(
        catalog_size=len(items),
        namespaces=len(namespaces),
        distractor_tools=distractors,
        destructive_tools=len(destructive),
        queries=report.queries_evaluated,
        recall_at_1=round(report.top_1_recall, 4),
        recall_at_3=round(report.top_3_recall, 4),
        recall_at_5=round(report.top_5_recall, 4),
        mrr=round(report.mrr, 4),
        mean_naive_tokens=naive_tokens,
        mean_card_tokens=mean_card,
        naive_prompt_chars=naive_chars,
        mean_card_chars=mean_card_chars,
        token_reduction_pct=reduction,
        destructive_in_shortlist_denied=leaked,
        namespace_filtered_recall_at_5=namespace_recall,
        namespace_filter_leaks=namespace_leaks,
        firewall_raw_chars=firewall.raw_chars,
        firewall_summary_chars=firewall.summary_chars,
        firewall_reduction_pct=firewall.reduction_pct,
        firewall_artifact_created=firewall.artifact_created,
        tool_view_recovered=firewall.tool_view_recovered,
        raw_result_exposed_inline=firewall.raw_exposed_inline,
        latency_ms_p50=round(_percentile(latencies, 50), 3),
        latency_ms_p99=round(_percentile(latencies, 99), 3),
    )


# ---------------------------------------------------------------------------
# Rendering (deterministic — no latency, no environment)
# ---------------------------------------------------------------------------


def to_json(result: LargeCatalogResult) -> dict[str, Any]:
    """Full result payload, including latency (for the JSON artifact only)."""
    return {
        "benchmark": "large_catalog",
        "seed": DEFAULT_SEED,
        "k": TOP_K,
        **result.__dict__,
    }


def render_scorecard(result: LargeCatalogResult) -> str:
    """Render the deterministic, latency-free scorecard markdown."""
    breaches = _threshold_breaches(result)
    status = "✅ within thresholds" if not breaches else "⚠️ " + "; ".join(breaches)
    return "\n".join(
        [
            "# contextweaver — Large-Catalog Routing Scorecard",
            "",
            "> Auto-generated by `make benchmark-large-catalog`. Do not edit by hand.",
            "> Source: `benchmarks/large_catalog.py` (issue #369). Offline, deterministic.",
            "",
            f"- Catalog size: `{result.catalog_size}` tools across "
            f"`{result.namespaces}` namespaces",
            f"- Near-duplicate distractor tools: `{result.distractor_tools}`",
            f"- Destructive (side-effecting) tools: `{result.destructive_tools}`",
            f"- Gold queries: `{result.queries}`",
            "- Token estimator: `CharDivFourEstimator` (no model dependency)",
            "",
            "## Routing accuracy",
            "",
            "| recall@1 | recall@3 | recall@5 | MRR |",
            "|---:|---:|---:|---:|",
            f"| {result.recall_at_1:.4f} | {result.recall_at_3:.4f} "
            f"| {result.recall_at_5:.4f} | {result.mrr:.4f} |",
            "",
            "## Prompt-token reduction (ChoiceCards vs naive all-tools prompt)",
            "",
            "| naive chars | mean card chars | naive tokens | mean card tokens | reduction |",
            "|---:|---:|---:|---:|---:|",
            f"| {result.naive_prompt_chars} | {result.mean_card_chars} "
            f"| {result.mean_naive_tokens} | {result.mean_card_tokens} "
            f"| {result.token_reduction_pct:.2f}% |",
            "",
            "## Destructive-tool filtering",
            "",
            f"- Destructive tools reaching the shortlist when denied: "
            f"`{result.destructive_in_shortlist_denied}` (expected `0`).",
            "",
            "## Namespace filtering",
            "",
            f"- recall@5 with the gold tool's namespace allowed: "
            f"`{result.namespace_filtered_recall_at_5:.4f}`.",
            f"- Cross-namespace candidates after gating: `{result.namespace_filter_leaks}` "
            "(expected `0`).",
            "",
            "## Result firewall and artifact view",
            "",
            "| raw chars | injected summary chars | reduction | artifact | "
            "raw inline | view recovered |",
            "|---:|---:|---:|:---:|:---:|:---:|",
            f"| {result.firewall_raw_chars} | {result.firewall_summary_chars} "
            f"| {result.firewall_reduction_pct:.2f}% "
            f"| {'yes' if result.firewall_artifact_created else 'no'} "
            f"| {'yes' if result.raw_result_exposed_inline else 'no'} "
            f"| {'yes' if result.tool_view_recovered else 'no'} |",
            "",
            "## Thresholds",
            "",
            f"- recall@5 floor: `{RECALL_AT_5_FLOOR:.2f}` · "
            f"token-reduction floor: `{TOKEN_REDUCTION_FLOOR_PCT:.0f}%`",
            f"- Status: {status}",
            "",
            "Latency is hardware-dependent and intentionally excluded from this",
            "committed scorecard; see `benchmarks/results/large_catalog.json` for the",
            "p50/p99 measured on the producing host.",
            "",
        ]
    )


def _threshold_breaches(result: LargeCatalogResult) -> list[str]:
    breaches: list[str] = []
    if result.recall_at_5 < RECALL_AT_5_FLOOR:
        breaches.append(f"recall@5 {result.recall_at_5:.4f} < {RECALL_AT_5_FLOOR:.2f}")
    if result.token_reduction_pct < TOKEN_REDUCTION_FLOOR_PCT:
        breaches.append(
            f"token reduction {result.token_reduction_pct:.2f}% < {TOKEN_REDUCTION_FLOOR_PCT:.0f}%"
        )
    if result.destructive_in_shortlist_denied:
        breaches.append(
            f"{result.destructive_in_shortlist_denied} denied destructive tool(s) leaked"
        )
    if result.namespace_filter_leaks:
        breaches.append(f"{result.namespace_filter_leaks} cross-namespace candidate(s) leaked")
    if not result.firewall_artifact_created:
        breaches.append("large result did not create a firewall artifact")
    if result.raw_result_exposed_inline:
        breaches.append("large raw result remained exposed inline")
    if not result.tool_view_recovered:
        breaches.append("tool_view could not recover the stored result")
    return breaches


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--size", type=int, default=DEFAULT_CATALOG_SIZE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--check", action="store_true", help="Exit non-zero on scorecard drift.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero if below thresholds.")
    args = parser.parse_args(argv)

    result = run_benchmark(args.size, args.seed)
    scorecard = render_scorecard(result)
    breaches = _threshold_breaches(result)

    if args.check:
        current = (
            DEFAULT_SCORECARD.read_text(encoding="utf-8") if DEFAULT_SCORECARD.exists() else ""
        )
        if current != scorecard:
            print(
                "large-catalog scorecard drift — run `make benchmark-large-catalog` and commit.",
                file=sys.stderr,
            )
            return 1
        print("large-catalog scorecard: up to date")
        if args.strict and breaches:
            print("WARNING: " + "; ".join(breaches), file=sys.stderr)
            return 1
        return 0

    DEFAULT_JSON.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_JSON.write_text(
        json.dumps(to_json(result), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    DEFAULT_SCORECARD.write_text(scorecard, encoding="utf-8", newline="\n")
    print(f"Wrote {DEFAULT_SCORECARD} and {DEFAULT_JSON}")
    print(
        f"recall@5={result.recall_at_5:.4f} reduction={result.token_reduction_pct:.2f}% "
        f"p99={result.latency_ms_p99:.3f}ms"
    )

    if breaches:
        print("WARNING: " + "; ".join(breaches), file=sys.stderr)
        if args.strict:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

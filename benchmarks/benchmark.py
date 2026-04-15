"""Benchmark harness for contextweaver routing and context pipeline.

Evaluates:
  Routing   — precision@k, recall@k, MRR, p50/p95/p99 latency across catalog sizes
  Context   — prompt_tokens, budget_utilization_pct, included/dropped/dedup counts,
              artifacts_created, avg_compaction_ratio

Catalog sizes tested: 50, ~83 (natural pool cap), 1000 (synthetic extension).
Deterministic: seeded RNG, sorted outputs.
No LLM calls or external network access.

Usage::

    python benchmarks/benchmark.py
    python benchmarks/benchmark.py --output benchmarks/results/custom.json --k 5

Exit codes: 0 on success, 1 on any error.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from contextweaver.config import ContextBudget  # noqa: E402
from contextweaver.context.manager import ContextManager  # noqa: E402
from contextweaver.protocols import CharDivFourEstimator  # noqa: E402
from contextweaver.routing.catalog import (  # noqa: E402
    generate_sample_catalog,
    load_catalog_dicts,
)
from contextweaver.routing.router import Router  # noqa: E402
from contextweaver.routing.tree import TreeBuilder  # noqa: E402
from contextweaver.store.event_log import InMemoryEventLog  # noqa: E402
from contextweaver.types import ContextItem, ItemKind, Phase, SelectableItem  # noqa: E402

_BENCH_DIR = Path(__file__).resolve().parent
_GOLD_PATH = _BENCH_DIR / "routing_gold.json"
_SCENARIOS_DIR = _BENCH_DIR / "scenarios"
_RESULTS_DIR = _BENCH_DIR / "results"

_BUDGET = ContextBudget(route=2000, call=4000, interpret=4000, answer=6000)
_ESTIMATOR = CharDivFourEstimator()

_KIND_MAP: dict[str, ItemKind] = {
    "user_turn": ItemKind.user_turn,
    "agent_msg": ItemKind.agent_msg,
    "tool_call": ItemKind.tool_call,
    "tool_result": ItemKind.tool_result,
}

# ---------------------------------------------------------------------------
# Catalog helpers
# ---------------------------------------------------------------------------


def _make_catalog(n: int, seed: int = 42) -> list[SelectableItem]:
    """Return a catalog of *n* SelectableItems.

    Extends the natural 83-item pool with synthetic variants when ``n > 83``.
    Synthetic variants carry the same tags and namespace as their originals,
    preserving routing signal density; they will not match gold-dataset queries
    (different IDs) so precision/recall measurements remain valid.
    """
    base_dicts = generate_sample_catalog(n=n, seed=seed)
    base_items = load_catalog_dicts(base_dicts)
    if n <= len(base_items):
        return sorted(base_items, key=lambda i: i.id)[:n]

    items: list[SelectableItem] = list(base_items)
    version = 2
    while len(items) < n:
        for orig in list(base_items):
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


def _build_router(items: list[SelectableItem]) -> Router:
    """Compile *items* into a TreeBuilder DAG and wrap with a Router."""
    graph = TreeBuilder().build(items)
    return Router(graph, items=items)


# ---------------------------------------------------------------------------
# Routing metrics
# ---------------------------------------------------------------------------


def _precision_at_k(predicted: list[str], expected: list[str], k: int) -> float:
    hits = sum(1 for p in predicted[:k] if p in expected)
    return hits / k if k > 0 else 0.0


def _recall_at_k(predicted: list[str], expected: list[str], k: int) -> float:
    if not expected:
        return 1.0
    hits = sum(1 for e in expected if e in set(predicted[:k]))
    return hits / len(expected)


def _reciprocal_rank(predicted: list[str], expected: list[str]) -> float:
    for rank, pid in enumerate(predicted, start=1):
        if pid in expected:
            return 1.0 / rank
    return 0.0


@dataclass
class RoutingStats:
    """Routing benchmark results for a single catalog size."""

    catalog_size: int
    queries_evaluated: int
    precision_at_k: float
    recall_at_k: float
    mrr: float
    latency_ms_p50: float
    latency_ms_p95: float
    latency_ms_p99: float


def _run_routing_benchmark(
    gold: list[dict[str, object]],
    catalog_sizes: list[int],
    k: int,
    seed: int,
    n_timing_runs: int,
) -> list[RoutingStats]:
    results: list[RoutingStats] = []
    for n in catalog_sizes:
        items = _make_catalog(n, seed=seed)
        item_ids = {it.id for it in items}
        router = _build_router(items)

        latencies_ms: list[float] = []
        precisions: list[float] = []
        recalls: list[float] = []
        rrs: list[float] = []

        for entry in gold:
            query = str(entry["query"])
            expected = [e for e in entry["expected"] if e in item_ids]  # type: ignore[union-attr]
            if not expected:
                continue

            last_result = None
            for _ in range(n_timing_runs):
                t0 = time.perf_counter()
                last_result = router.route(query)
                latencies_ms.append((time.perf_counter() - t0) * 1000)

            if last_result is None:
                continue
            predicted = last_result.candidate_ids
            precisions.append(_precision_at_k(predicted, expected, k))
            recalls.append(_recall_at_k(predicted, expected, k))
            rrs.append(_reciprocal_rank(predicted, expected))

        latencies_ms.sort()
        _lat = list(latencies_ms)
        _n = len(_lat)

        def _pct(pct: float, lats: list[float] = _lat, n: int = _n) -> float:
            if not n:
                return 0.0
            idx = min(int(n * pct), n - 1)
            return round(lats[idx], 3)

        results.append(
            RoutingStats(
                catalog_size=len(items),
                queries_evaluated=len(precisions),
                precision_at_k=round(statistics.mean(precisions), 4) if precisions else 0.0,
                recall_at_k=round(statistics.mean(recalls), 4) if recalls else 0.0,
                mrr=round(statistics.mean(rrs), 4) if rrs else 0.0,
                latency_ms_p50=_pct(0.50),
                latency_ms_p95=_pct(0.95),
                latency_ms_p99=_pct(0.99),
            )
        )
    return results


# ---------------------------------------------------------------------------
# Context pipeline metrics
# ---------------------------------------------------------------------------


@dataclass
class ContextStats:
    """Context pipeline benchmark results for a single scenario."""

    scenario: str
    event_count: int
    items_included: int
    items_dropped: int
    dedup_removed: int
    prompt_tokens: int
    budget_tokens: int
    budget_utilization_pct: float
    artifacts_created: int
    avg_compaction_ratio: float


def _load_scenario(path: Path) -> list[ContextItem]:
    """Deserialise a scenario JSONL file into ContextItems."""
    items: list[ContextItem] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        raw = json.loads(line)
        kind = _KIND_MAP.get(raw.get("type", ""), ItemKind.user_turn)
        items.append(
            ContextItem(
                id=raw["id"],
                kind=kind,
                text=raw.get("text", ""),
                parent_id=raw.get("parent_id"),
            )
        )
    return items


def _run_context_benchmark(scenario_paths: list[Path]) -> list[ContextStats]:
    results: list[ContextStats] = []
    for path in sorted(scenario_paths):
        events = _load_scenario(path)
        log = InMemoryEventLog()
        for ev in events:
            log.append(ev)

        mgr = ContextManager(event_log=log, budget=_BUDGET, estimator=_ESTIMATOR)
        # Use the last user turn text as query, falling back to empty string
        query = next((ev.text for ev in reversed(events) if ev.kind == ItemKind.user_turn), "")
        pack = mgr.build_sync(phase=Phase.answer, query=query)

        prompt_toks = _ESTIMATOR.estimate(pack.prompt)
        budget_toks = _BUDGET.for_phase(Phase.answer)

        ratios: list[float] = []
        for env in pack.envelopes:
            if env.artifacts:
                raw_bytes = env.artifacts[0].size_bytes
                summary_bytes = len(env.summary.encode())
                if summary_bytes > 0:
                    ratios.append(raw_bytes / summary_bytes)

        results.append(
            ContextStats(
                scenario=path.stem,
                event_count=len(events),
                items_included=pack.stats.included_count,
                items_dropped=pack.stats.dropped_count,
                dedup_removed=pack.stats.dedup_removed,
                prompt_tokens=prompt_toks,
                budget_tokens=budget_toks,
                budget_utilization_pct=round(prompt_toks / budget_toks * 100, 1),
                artifacts_created=len(mgr.artifact_store.list_refs()),
                avg_compaction_ratio=round(statistics.mean(ratios), 2) if ratios else 0.0,
            )
        )
    return results


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _print_routing_table(rows: list[RoutingStats], k: int) -> None:
    header = (
        f"{'catalog_size':>12}  {'queries':>7}  {'prec@' + str(k):>8}  {'recall@' + str(k):>9}"
        f"  {'mrr':>6}  {'p50_ms':>7}  {'p95_ms':>7}  {'p99_ms':>7}"
    )
    sep = "-" * len(header)
    print("\n=== Routing Benchmark ===")
    print(header)
    print(sep)
    for r in rows:
        print(
            f"{r.catalog_size:>12}  {r.queries_evaluated:>7}  {r.precision_at_k:>8.4f}"
            f"  {r.recall_at_k:>9.4f}  {r.mrr:>6.4f}"
            f"  {r.latency_ms_p50:>7.3f}  {r.latency_ms_p95:>7.3f}  {r.latency_ms_p99:>7.3f}"
        )


def _print_context_table(rows: list[ContextStats]) -> None:
    header = (
        f"{'scenario':<22}  {'events':>6}  {'incl':>5}  {'drop':>5}  {'dedup':>5}"
        f"  {'tok':>5}  {'util%':>6}  {'arts':>4}  {'compact':>8}"
    )
    sep = "-" * len(header)
    print("\n=== Context Pipeline Benchmark ===")
    print(header)
    print(sep)
    for r in rows:
        print(
            f"{r.scenario:<22}  {r.event_count:>6}  {r.items_included:>5}  {r.items_dropped:>5}"
            f"  {r.dedup_removed:>5}  {r.prompt_tokens:>5}  {r.budget_utilization_pct:>5.1f}%"
            f"  {r.artifacts_created:>4}  {r.avg_compaction_ratio:>7.2f}x"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="contextweaver benchmark harness",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--output",
        default=str(_RESULTS_DIR / "latest.json"),
        help="Path to write JSON results",
    )
    parser.add_argument("--k", type=int, default=5, help="Rank cutoff for precision/recall")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed for determinism")
    parser.add_argument(
        "--timing-runs",
        type=int,
        default=3,
        help="Routing query repetitions per query for latency measurement",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the full benchmark suite and print results."""
    args = _parse_args(argv)

    # Load gold dataset
    gold: list[dict[str, object]] = json.loads(_GOLD_PATH.read_text(encoding="utf-8"))

    # Catalog sizes: 50, natural cap (~83), and 1000-item synthetic extension
    catalog_sizes = [50, 83, 1000]

    # Scenario files
    scenario_paths = sorted(_SCENARIOS_DIR.glob("*.jsonl"))
    if not scenario_paths:
        print("No scenario files found in benchmarks/scenarios/", file=sys.stderr)
        return 1

    print(f"Gold queries: {len(gold)}  |  Catalog sizes: {catalog_sizes}  |  k={args.k}")
    print(f"Scenarios: {[p.name for p in scenario_paths]}")

    routing_results = _run_routing_benchmark(
        gold=gold,
        catalog_sizes=catalog_sizes,
        k=args.k,
        seed=args.seed,
        n_timing_runs=args.timing_runs,
    )
    context_results = _run_context_benchmark(scenario_paths)

    _print_routing_table(routing_results, args.k)
    _print_context_table(context_results)

    out = {
        "benchmark_version": "1.0",
        "k": args.k,
        "seed": args.seed,
        "routing": [asdict(r) for r in routing_results],
        "context": [asdict(r) for r in context_results],
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nResults written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

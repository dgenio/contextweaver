"""Benchmark harness for contextweaver routing and context pipeline.

Evaluates:
  Routing   — precision@k, recall@k, MRR, p50/p95/p99 latency across catalog sizes
              and (optionally) per backend (tfidf / bm25 / fuzzy) and per namespace
  Context   — prompt_tokens, budget_utilization_pct, included/dropped/dedup counts,
              artifacts_created, avg_compaction_ratio, naive-concat token delta

Catalog sizes tested:
  - Legacy ``routing`` summary: 50, ~83 (natural pool cap), 1000 (synthetic extension)
  - Matrix ``routing_matrix``: 100, 500, 1000 (overridable via ``--sizes``)

Deterministic: seeded RNG, sorted outputs.
No LLM calls or external network access.

Usage::

    python benchmarks/benchmark.py
    python benchmarks/benchmark.py --output benchmarks/results/custom.json --k 5
    # Full per-backend × per-size matrix (issue #208):
    python benchmarks/benchmark.py --matrix
    python benchmarks/benchmark.py --matrix --backends tfidf,bm25 --sizes 100,500

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
from contextweaver._utils import FuzzyScorer  # noqa: E402
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


def _build_router(items: list[SelectableItem], scorer_backend: str = "tfidf") -> Router:
    """Compile *items* into a TreeBuilder DAG and wrap with a Router.

    Args:
        items: Catalog items to compile into the routing DAG.
        scorer_backend: One of ``tfidf`` / ``bm25`` / ``fuzzy``. The ``fuzzy``
            backend requires the ``[retrieval]`` extra and will raise a
            :class:`~contextweaver.exceptions.ConfigError` when missing — callers
            that want to skip rather than fail should pre-check
            :data:`_FUZZY_AVAILABLE`.
    """
    graph = TreeBuilder().build(items)
    return Router(graph, items=items, scorer_backend=scorer_backend)


# ``FuzzyScorer`` is the runtime ``None`` sentinel exposed by ``_utils`` when
# the ``[retrieval]`` extra is missing. The matrix runner uses this to record
# a ``"status": "skipped: missing rapidfuzz"`` row rather than crash (#208).
_FUZZY_AVAILABLE: bool = FuzzyScorer is not None


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


@dataclass
class MatrixCell:
    """One row in ``routing_matrix`` — a (backend, catalog_size) cell.

    See issue #208. The cell carries the same accuracy / latency metrics as
    :class:`RoutingStats` plus the routing backend identity, and gains a
    ``status`` field used to record graceful skips (e.g. fuzzy backend when
    the ``[retrieval]`` extra is missing).
    """

    backend: str
    catalog_size: int
    queries_evaluated: int
    precision_at_k: float
    recall_at_k: float
    mrr: float
    latency_ms_p50: float
    latency_ms_p95: float
    latency_ms_p99: float
    status: str = "ok"


@dataclass
class NamespaceCell:
    """Per-namespace recall@k breakdown (issue #209)."""

    backend: str
    catalog_size: int
    namespace: str
    queries_evaluated: int
    recall_at_k: float


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
# Per-backend × per-size matrix (issue #208) with per-namespace breakdown (#209)
# ---------------------------------------------------------------------------


def _entry_namespace(entry: dict[str, object]) -> str:
    """Derive a stable namespace label for a gold entry.

    Prefers the explicit ``namespace`` field added in #209; falls back to the
    dot-prefix of the first ``expected`` id for legacy entries.
    """
    ns = entry.get("namespace")
    if isinstance(ns, str) and ns:
        return ns
    expected = entry.get("expected")
    if isinstance(expected, list) and expected:
        first = expected[0]
        if isinstance(first, str) and "." in first:
            return first.split(".", 1)[0]
    return "unknown"


def _percentile(sorted_lats: list[float], pct: float) -> float:
    """Return ``pct``-th percentile of pre-sorted latency samples (ms)."""
    if not sorted_lats:
        return 0.0
    idx = min(int(len(sorted_lats) * pct), len(sorted_lats) - 1)
    return round(sorted_lats[idx], 3)


def _run_matrix_cell(
    gold: list[dict[str, object]],
    backend: str,
    catalog_size: int,
    k: int,
    seed: int,
    n_timing_runs: int,
) -> tuple[MatrixCell, list[NamespaceCell]]:
    """Run one (backend, catalog_size) cell and return its row + per-namespace rows."""
    if backend == "fuzzy" and not _FUZZY_AVAILABLE:
        skipped = MatrixCell(
            backend=backend,
            catalog_size=catalog_size,
            queries_evaluated=0,
            precision_at_k=0.0,
            recall_at_k=0.0,
            mrr=0.0,
            latency_ms_p50=0.0,
            latency_ms_p95=0.0,
            latency_ms_p99=0.0,
            status="skipped: missing rapidfuzz",
        )
        return skipped, []

    items = _make_catalog(catalog_size, seed=seed)
    item_ids = {it.id for it in items}
    router = _build_router(items, scorer_backend=backend)

    latencies_ms: list[float] = []
    precisions: list[float] = []
    recalls: list[float] = []
    rrs: list[float] = []
    # Per-namespace accumulator: ns -> list[recall_at_k]
    ns_recalls: dict[str, list[float]] = {}

    for entry in gold:
        query = str(entry["query"])
        raw_expected = entry.get("expected", [])
        if not isinstance(raw_expected, list):
            continue
        expected = [e for e in raw_expected if isinstance(e, str) and e in item_ids]
        if not expected:
            continue
        ns = _entry_namespace(entry)

        last_result = None
        for _ in range(n_timing_runs):
            t0 = time.perf_counter()
            last_result = router.route(query)
            latencies_ms.append((time.perf_counter() - t0) * 1000)
        if last_result is None:
            continue

        predicted = last_result.candidate_ids
        rec = _recall_at_k(predicted, expected, k)
        precisions.append(_precision_at_k(predicted, expected, k))
        recalls.append(rec)
        rrs.append(_reciprocal_rank(predicted, expected))
        ns_recalls.setdefault(ns, []).append(rec)

    latencies_ms.sort()
    cell = MatrixCell(
        backend=backend,
        catalog_size=len(items),
        queries_evaluated=len(precisions),
        precision_at_k=round(statistics.mean(precisions), 4) if precisions else 0.0,
        recall_at_k=round(statistics.mean(recalls), 4) if recalls else 0.0,
        mrr=round(statistics.mean(rrs), 4) if rrs else 0.0,
        latency_ms_p50=_percentile(latencies_ms, 0.50),
        latency_ms_p95=_percentile(latencies_ms, 0.95),
        latency_ms_p99=_percentile(latencies_ms, 0.99),
        status="ok",
    )
    ns_rows = [
        NamespaceCell(
            backend=backend,
            catalog_size=len(items),
            namespace=ns,
            queries_evaluated=len(rs),
            recall_at_k=round(statistics.mean(rs), 4) if rs else 0.0,
        )
        for ns, rs in sorted(ns_recalls.items())
    ]
    return cell, ns_rows


def _run_matrix(
    gold: list[dict[str, object]],
    backends: list[str],
    catalog_sizes: list[int],
    k: int,
    seed: int,
    n_timing_runs: int,
) -> tuple[list[MatrixCell], list[NamespaceCell]]:
    """Run the full ``backends × catalog_sizes`` matrix (issue #208).

    Returns a flat list of :class:`MatrixCell` rows (sorted deterministically by
    ``(backend, catalog_size)``) plus a flat list of :class:`NamespaceCell`
    rows for the per-namespace breakdown (#209).
    """
    cells: list[MatrixCell] = []
    ns_cells: list[NamespaceCell] = []
    for backend in sorted(backends):
        for n in sorted(catalog_sizes):
            cell, ns_rows = _run_matrix_cell(
                gold=gold,
                backend=backend,
                catalog_size=n,
                k=k,
                seed=seed,
                n_timing_runs=n_timing_runs,
            )
            cells.append(cell)
            ns_cells.extend(ns_rows)
    return cells, ns_cells


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


def _print_matrix_table(cells: list[MatrixCell], k: int) -> None:
    """Print the per-backend × per-size matrix (issue #208)."""
    header = (
        f"{'backend':<8}  {'catalog_size':>12}  {'queries':>7}  {'prec@' + str(k):>8}"
        f"  {'recall@' + str(k):>9}  {'mrr':>6}  {'p50_ms':>7}  {'p95_ms':>7}"
        f"  {'p99_ms':>7}  {'status':<32}"
    )
    sep = "-" * len(header)
    print("\n=== Routing Matrix (per-backend × per-size) ===")
    print(header)
    print(sep)
    for c in cells:
        print(
            f"{c.backend:<8}  {c.catalog_size:>12}  {c.queries_evaluated:>7}"
            f"  {c.precision_at_k:>8.4f}  {c.recall_at_k:>9.4f}  {c.mrr:>6.4f}"
            f"  {c.latency_ms_p50:>7.3f}  {c.latency_ms_p95:>7.3f}"
            f"  {c.latency_ms_p99:>7.3f}  {c.status:<32}"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


_DEFAULT_MATRIX_BACKENDS = "tfidf,bm25,fuzzy"
_SUPPORTED_BACKENDS = frozenset({"tfidf", "bm25", "fuzzy"})
_DEFAULT_MATRIX_SIZES = "100,500,1000"


def _csv_int_list(raw: str) -> list[int]:
    """Parse a ``"100,500,1000"``-style flag into a sorted list of ints."""
    if not raw:
        return []
    out: list[int] = []
    for piece in raw.split(","):
        piece = piece.strip()
        if piece:
            out.append(int(piece))
    return out


def _csv_str_list(raw: str) -> list[str]:
    """Parse a ``"tfidf,bm25"``-style flag into a list of names."""
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


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
    parser.add_argument(
        "--matrix",
        action="store_true",
        help="Also emit a per-backend × per-size matrix (issue #208).",
    )
    parser.add_argument(
        "--backends",
        default=_DEFAULT_MATRIX_BACKENDS,
        help=(
            "Comma-separated routing backends for the matrix run "
            "(any of: tfidf, bm25, fuzzy). The 'fuzzy' backend requires "
            "the [retrieval] extra; missing backends are recorded with "
            "an explicit 'status: skipped' row rather than silently omitted."
        ),
    )
    parser.add_argument(
        "--sizes",
        default=_DEFAULT_MATRIX_SIZES,
        help="Comma-separated catalog sizes for the matrix run.",
    )
    parser.add_argument(
        "--no-naive-delta",
        action="store_true",
        help=(
            "Disable per-scenario naïve-concat baseline measurement (issue #215). "
            "Default is enabled; the naive_delta block is additive to each context row."
        ),
    )
    args = parser.parse_args(argv)
    # Fail fast on typos like `--backends tifdf,bm25` rather than letting the
    # bad name reach Router init and surface as a ConfigError traceback.
    requested = set(_csv_str_list(args.backends))
    unknown = sorted(requested - _SUPPORTED_BACKENDS)
    if unknown:
        parser.error(
            f"unsupported --backends value(s): {', '.join(unknown)}. "
            f"Choose from: {', '.join(sorted(_SUPPORTED_BACKENDS))}."
        )
    return args


def main(argv: list[str] | None = None) -> int:
    """Run the full benchmark suite and print results."""
    args = _parse_args(argv)

    # Load gold dataset
    gold: list[dict[str, object]] = json.loads(_GOLD_PATH.read_text(encoding="utf-8"))

    # Catalog sizes for the legacy single-backend ``routing`` summary list.
    # The matrix runner (#208) uses its own ``--sizes`` (default 100/500/1000).
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

    # Optional per-backend × per-size matrix + per-namespace breakdown (#208, #209).
    matrix_cells: list[MatrixCell] = []
    ns_cells: list[NamespaceCell] = []
    if args.matrix:
        backends = _csv_str_list(args.backends) or _csv_str_list(_DEFAULT_MATRIX_BACKENDS)
        sizes = _csv_int_list(args.sizes) or _csv_int_list(_DEFAULT_MATRIX_SIZES)
        matrix_cells, ns_cells = _run_matrix(
            gold=gold,
            backends=backends,
            catalog_sizes=sizes,
            k=args.k,
            seed=args.seed,
            n_timing_runs=args.timing_runs,
        )
        _print_matrix_table(matrix_cells, args.k)

    # Optional naïve-concat baseline (#215) — additive ``naive_delta`` per context row.
    context_dicts = [asdict(r) for r in context_results]
    if not args.no_naive_delta:
        # Import is local because ``scripts/`` is not a package and the
        # naïve-baseline implementation deliberately lives outside the library.
        sys.path.insert(0, str(_ROOT / "scripts"))
        from baseline_naive import compute_naive_delta  # noqa: E402,PLC0415

        scenario_index = {p.stem: p for p in scenario_paths}
        for row in context_dicts:
            scenario_path = scenario_index.get(str(row.get("scenario", "")))
            if scenario_path is None:
                continue
            row["naive_delta"] = compute_naive_delta(scenario_path, row)

    out = {
        "benchmark_version": "1.1",
        "k": args.k,
        "seed": args.seed,
        "routing": [asdict(r) for r in routing_results],
        "context": context_dicts,
    }
    if matrix_cells:
        out["routing_matrix"] = [asdict(c) for c in matrix_cells]
    if ns_cells:
        out["routing_per_namespace"] = [asdict(c) for c in ns_cells]

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nResults written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

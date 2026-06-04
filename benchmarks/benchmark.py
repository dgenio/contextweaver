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
import functools
import json
import os
import platform
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from contextweaver._utils import FuzzyScorer  # noqa: E402
from contextweaver.config import ContextBudget  # noqa: E402
from contextweaver.context.manager import ContextManager  # noqa: E402
from contextweaver.eval.metrics import (  # noqa: E402
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
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


# ``_MIXED_NAMESPACE_SHAPE`` defines the head-heavy + long-tail namespace
# distribution used by :func:`_make_mixed_namespace_catalog` (issue #277).
# Tuples are ``(namespace_label, item_count)``.  The shape is intentionally
# asymmetric to contrast with the uniform 8-namespace catalog produced by
# :func:`_make_catalog`: one head namespace dominates, two mids carry roughly
# a quarter, four small namespaces share the remainder, and a long-tail of
# single-item namespaces simulates niche production tools.
_MIXED_NAMESPACE_HEAD = ("analytics_xl", 200)
_MIXED_NAMESPACE_MID = (("billing_xl", 60), ("crm_xl", 40))
_MIXED_NAMESPACE_SMALL = (
    ("admin_xl", 25),
    ("comms_xl", 25),
    ("docs_xl", 25),
    ("infra_xl", 25),
)
_MIXED_NAMESPACE_TAIL_NS_COUNT = 100  # 100 namespaces × 1 item each


def _make_mixed_namespace_catalog(n: int = 500, seed: int = 42) -> list[SelectableItem]:
    """Return a catalog of *n* items with a head-heavy + long-tail namespace shape.

    Issue #277.  Real production catalogs are rarely shaped like the uniform
    8-namespace pool used by :func:`_make_catalog` — they typically have one
    dominant namespace (analytics / observability), a couple of mid-weight
    ones (billing / crm), a handful of small operational namespaces, and a
    long tail of niche single-tool namespaces.

    The natural 83-item gold-pool is kept intact under its original
    namespaces so the gold-dataset queries still match.  Additional items
    are synthesised with the head-heavy distribution defined by
    :data:`_MIXED_NAMESPACE_HEAD` / ``_MID`` / ``_SMALL`` /
    ``_TAIL_NS_COUNT`` so the routing layer sees a deliberately uneven
    namespace surface.  The plan declares 100 long-tail namespaces but
    the *n*-cap truncates the tail once the head/mid/small segments fill
    their quota (at n=500 only ~17 tail namespaces survive).  Synthetic
    items carry distinct IDs so they will not match gold-dataset
    queries — precision/recall remains valid.

    Args:
        n: Total catalog size.  Default 500 matches the issue's request.
        seed: RNG seed for the underlying ``generate_sample_catalog`` call.

    Returns:
        Sorted list of ``SelectableItem`` instances, exactly *n* long.
    """
    base_dicts = generate_sample_catalog(n=min(n, 83), seed=seed)
    base_items = load_catalog_dicts(base_dicts)
    items: list[SelectableItem] = list(base_items)

    head_label, head_n = _MIXED_NAMESPACE_HEAD
    plan: list[tuple[str, int]] = [(head_label, head_n)]
    plan.extend(_MIXED_NAMESPACE_MID)
    plan.extend(_MIXED_NAMESPACE_SMALL)
    for i in range(_MIXED_NAMESPACE_TAIL_NS_COUNT):
        plan.append((f"longtail_{i:03d}", 1))

    # Synthetic prototype text per namespace — deterministic but distinct
    # so each namespace's items have intra-namespace lexical signal.
    for ns_label, count in plan:
        for j in range(count):
            if len(items) >= n:
                break
            item_id = f"{ns_label}.tool_{j:03d}"
            items.append(
                SelectableItem(
                    item_id,
                    "tool",
                    f"{ns_label}_tool_{j}",
                    f"Synthetic {ns_label} operation #{j} for shape diversity benchmark",
                    tags=[ns_label],
                    namespace=ns_label,
                )
            )
        if len(items) >= n:
            break

    return sorted(items, key=lambda i: i.id)[:n]


def _build_router(items: list[SelectableItem], scorer_backend: str = "tfidf") -> Router:
    """Compile *items* into a TreeBuilder DAG and wrap with a Router.

    Args:
        items: Catalog items to compile into the routing DAG.
        scorer_backend: One of ``tfidf`` / ``bm25`` / ``fuzzy`` / ``embedding_hashing``
            / ``embedding_st``.  The ``fuzzy`` backend requires the
            ``[retrieval]`` extra; ``embedding_st`` requires the
            ``[embeddings]`` extra.  Both raise a
            :class:`~contextweaver.exceptions.ConfigError` (or ``ImportError``)
            when missing — callers that want to skip rather than fail should
            pre-check :data:`_FUZZY_AVAILABLE` / :data:`_SENTENCE_TRANSFORMERS_AVAILABLE`.
    """
    graph = TreeBuilder().build(items)
    if scorer_backend == "embedding_hashing":
        # Stdlib-only embedding backend — always available; provides the
        # baseline embedding-path row in the scorecard (#266).
        from contextweaver.extras.embeddings import HashingEmbeddingBackend

        return Router(graph, items=items, embedding_backend=HashingEmbeddingBackend())
    if scorer_backend == "embedding_st":
        # Real sentence-transformers backend — requires [embeddings] extra (#266).
        from contextweaver.extras.embeddings import SentenceTransformerBackend

        return Router(graph, items=items, embedding_backend=SentenceTransformerBackend())
    return Router(graph, items=items, scorer_backend=scorer_backend)


# ``FuzzyScorer`` is the runtime ``None`` sentinel exposed by ``_utils`` when
# the ``[retrieval]`` extra is missing. The matrix runner uses this to record
# a ``"status": "skipped: missing rapidfuzz"`` row rather than crash (#208).
_FUZZY_AVAILABLE: bool = FuzzyScorer is not None


@functools.cache
def _sentence_transformers_available() -> bool:
    """Return True iff the ``[embeddings]`` extra is importable (#266).

    Used by the matrix runner to emit a ``"skipped: missing sentence-transformers"``
    row for the ``embedding_st`` backend rather than crash on a fresh install.

    The check is wrapped in :func:`functools.cache` so the
    ``sentence_transformers`` import is performed **at most once per
    process**, and only when something actually asks (e.g. the matrix
    runner about to launch an ``embedding_st`` cell).  This avoids
    paying the import cost on every plain ``python benchmarks/benchmark.py``
    invocation when the ``[embeddings]`` extra happens to be installed.
    """
    try:
        import sentence_transformers  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


def _matrix_cell_skip_reason(backend: str) -> str | None:
    """Return a ``"skipped: ..."`` status string when *backend* cannot run, else ``None``.

    Drives the graceful-skip path in :func:`_run_matrix_cell` for backends
    whose runtime requires an optional extra (``rapidfuzz`` for the
    ``fuzzy`` backend, ``sentence-transformers`` for the ``embedding_st``
    backend).  Centralising the policy here keeps the cell runner short
    and makes it trivial to extend with future backends (#266, #208).
    The ``sentence_transformers`` availability check is deferred — the
    import only runs when a matrix cell actually targets the
    ``embedding_st`` backend.
    """
    if backend == "fuzzy" and not _FUZZY_AVAILABLE:
        return "skipped: missing rapidfuzz"
    if backend == "embedding_st" and not _sentence_transformers_available():
        return "skipped: missing sentence-transformers"
    return None


# ---------------------------------------------------------------------------
# Routing metrics
# ---------------------------------------------------------------------------
#
# precision@k / recall@k / reciprocal_rank are imported from
# ``contextweaver.eval.metrics`` — the single source of truth shared with the
# library evaluation harness (``contextweaver.eval.routing``) so the two can no
# longer drift (issue #354).  ``recall_at_k`` here is the same classic
# fractional definition this script has always used, so scorecard numbers are
# unchanged by the consolidation.


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
            precisions.append(precision_at_k(predicted, expected, k))
            recalls.append(recall_at_k(predicted, expected, k))
            rrs.append(reciprocal_rank(predicted, expected))

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
    catalog_factory: Callable[[int, int], list[SelectableItem]] = _make_catalog,
) -> tuple[MatrixCell, list[NamespaceCell]]:
    """Run one (backend, catalog_size) cell and return its row + per-namespace rows.

    Args:
        gold: The gold dataset rows (see ``benchmarks/routing_gold.json``).
        backend: One of the entries in :data:`_SUPPORTED_BACKENDS`.
        catalog_size: Total items in the catalog for this cell.
        k: Rank cutoff for precision/recall/MRR.
        seed: RNG seed forwarded to the catalog factory.
        n_timing_runs: Routing-query repetitions per gold entry (latency
            stabilisation).
        catalog_factory: Function ``(n, seed) -> list[SelectableItem]``
            used to build the catalog for this cell.  Defaults to the
            uniform 8-namespace pool (:func:`_make_catalog`); the
            mixed-shape matrix runner (#277) passes
            :func:`_make_mixed_namespace_catalog`.
    """
    skip_reason = _matrix_cell_skip_reason(backend)
    if skip_reason is not None:
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
            status=skip_reason,
        )
        return skipped, []

    items = catalog_factory(catalog_size, seed)
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
        rec = recall_at_k(predicted, expected, k)
        precisions.append(precision_at_k(predicted, expected, k))
        recalls.append(rec)
        rrs.append(reciprocal_rank(predicted, expected))
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


def _run_matrix_mixed_shape(
    gold: list[dict[str, object]],
    backends: list[str],
    k: int,
    seed: int,
    n_timing_runs: int,
) -> list[MatrixCell]:
    """Run the matrix against the head-heavy mixed-namespace catalog (issue #277).

    Always uses ``catalog_size = 500`` since the mixed-shape distribution
    is calibrated to that size (the head namespace has 200 items, mids 100,
    smalls 100, long-tail 100; see :func:`_make_mixed_namespace_catalog`).
    Per-namespace rows are intentionally not emitted — the long-tail of
    100 single-item namespaces would dominate the table with zero-recall
    rows; the gold dataset only covers the natural 8 namespaces.
    """
    cells: list[MatrixCell] = []
    for backend in sorted(backends):
        cell, _ = _run_matrix_cell(
            gold=gold,
            backend=backend,
            catalog_size=500,
            k=k,
            seed=seed,
            n_timing_runs=n_timing_runs,
            catalog_factory=_make_mixed_namespace_catalog,
        )
        cells.append(cell)
    return cells


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
# Environment capture (issue #267)
# ---------------------------------------------------------------------------

# Pinned hardware reference rig the scorecard's absolute-latency numbers are
# meant to be read against.  Chosen to match a stock GitHub Actions
# ``ubuntu-latest`` runner so any reader can reproduce the order-of-magnitude
# numbers in cloud CI without owning a specific machine.  When the harness
# runs on a different host, the renderer emits both the pinned rig and the
# measured environment so callers can see the divergence (#267).
_REFERENCE_RIG = {
    "label": "GitHub Actions ubuntu-latest (2-core x86_64)",
    "system": "Linux",
    "machine": "x86_64",
    "cpu_logical_cores": 2,
    "python_version": "3.10+",
    "notes": (
        "Absolute latency on other hardware will differ; the *relative* "
        "cost between catalog sizes and backends is portable."
    ),
}


@dataclass
class EnvironmentInfo:
    """Best-effort metadata about the machine that produced ``latest.json``.

    Captures stdlib-resolvable identifiers only — no third-party probing,
    no network calls.  The renderer uses this against
    :data:`_REFERENCE_RIG` to emit the "Measured on" disclosure under the
    "Hardware reference rig" section (#267).
    """

    system: str
    machine: str
    processor: str
    python_version: str
    python_implementation: str
    cpu_logical_cores: int
    platform_string: str


def _capture_environment() -> EnvironmentInfo:
    """Return a stdlib snapshot of the host environment (issue #267)."""
    return EnvironmentInfo(
        system=platform.system(),
        machine=platform.machine(),
        processor=platform.processor() or "unknown",
        python_version=platform.python_version(),
        python_implementation=platform.python_implementation(),
        cpu_logical_cores=os.cpu_count() or 0,
        platform_string=platform.platform(),
    )


# ---------------------------------------------------------------------------
# Tiktoken parity check (issue #268)
# ---------------------------------------------------------------------------


@dataclass
class TiktokenParityStats:
    """``CharDivFourEstimator`` vs ``cl100k_base`` drift on the gold corpus.

    Reported metrics:

    * ``mean_abs_error`` — mean ``|cw - tiktoken|`` token-count difference.
    * ``max_abs_error`` — worst-case absolute drift in a single sample.
    * ``mean_signed_error`` — mean ``cw - tiktoken``; positive means the
      stdlib estimator over-counts, negative means it under-counts.
    * ``mean_ratio`` — mean ``cw / tiktoken``; ``1.0`` means perfect parity.
    * ``samples`` — number of inputs evaluated.
    * ``status`` — ``"ok"`` when ``tiktoken`` is importable and the
      ``cl100k_base`` encoding is reachable, otherwise a ``"skipped: ..."``
      explanation suitable for the scorecard.
    """

    samples: int
    mean_abs_error: float
    max_abs_error: int
    mean_signed_error: float
    mean_ratio: float
    status: str = "ok"


def _gold_corpus_for_parity(gold: list[dict[str, object]]) -> list[str]:
    """Flatten the gold dataset's ``query`` strings for token counting.

    Returns the non-empty ``query`` field from each gold entry.  This is
    the corpus distribution ``CharDivFourEstimator`` is asked to estimate
    on most often in the routing pipeline (the router receives a query
    string and counts tokens against the route-phase budget).  Tool
    descriptions are *not* included because they're long-form prose that
    skews well within the estimator's tested range; the parity metric is
    most informative on the short, idiomatic query distribution.
    """
    out: list[str] = []
    for entry in gold:
        q = entry.get("query")
        if isinstance(q, str) and q:
            out.append(q)
    return out


def _run_tiktoken_parity(gold: list[dict[str, object]]) -> TiktokenParityStats:
    """Quantify ``CharDivFourEstimator`` vs real ``cl100k_base`` drift (#268).

    Returns a :class:`TiktokenParityStats` with ``status='skipped: ...'``
    when ``tiktoken`` is unavailable or the encoding cannot load (e.g. an
    offline CI without the cached encoding); callers don't need to guard
    individually.
    """
    try:
        import tiktoken  # noqa: PLC0415 — optional / lazy import

        encoding = tiktoken.get_encoding("cl100k_base")
    except Exception as exc:  # pragma: no cover - exercised only when offline
        return TiktokenParityStats(
            samples=0,
            mean_abs_error=0.0,
            max_abs_error=0,
            mean_signed_error=0.0,
            mean_ratio=0.0,
            status=f"skipped: {type(exc).__name__}",
        )

    corpus = _gold_corpus_for_parity(gold)
    if not corpus:
        return TiktokenParityStats(
            samples=0,
            mean_abs_error=0.0,
            max_abs_error=0,
            mean_signed_error=0.0,
            mean_ratio=0.0,
            status="skipped: empty corpus",
        )

    abs_errs: list[int] = []
    signed_errs: list[int] = []
    ratios: list[float] = []
    for text in corpus:
        true_n = len(encoding.encode(text))
        cw_n = _ESTIMATOR.estimate(text)
        abs_errs.append(abs(cw_n - true_n))
        signed_errs.append(cw_n - true_n)
        ratios.append(cw_n / true_n if true_n > 0 else 0.0)

    return TiktokenParityStats(
        samples=len(corpus),
        mean_abs_error=round(statistics.mean(abs_errs), 4),
        max_abs_error=max(abs_errs),
        mean_signed_error=round(statistics.mean(signed_errs), 4),
        mean_ratio=round(statistics.mean(ratios), 4),
        status="ok",
    )


# ---------------------------------------------------------------------------
# Optional end-to-end real-model benchmark (issue #269) — offline by default
# ---------------------------------------------------------------------------


@dataclass
class E2ERealModelStats:
    """Optional end-to-end cost / latency capture against a real model (#269).

    Off by default; runs only when ``--with-real-model`` is passed **and**
    ``CW_BENCH_LLM_PROVIDER`` + ``CW_BENCH_LLM_API_KEY`` are both set in the
    environment.  Even then the harness calls a single model endpoint per
    gold-query batch — there is no fan-out, no retry storm, no schema fuzz.

    When disabled, the harness still emits the dataclass with
    ``status='skipped: offline by default'`` so the scorecard's E2E section
    documents how to enable the capture without ever pulling a key into CI.
    """

    provider: str = ""
    model: str = ""
    samples: int = 0
    prompt_tokens_total: int = 0
    completion_tokens_total: int = 0
    estimated_usd_cost: float = 0.0
    e2e_latency_ms_p50: float = 0.0
    e2e_latency_ms_p95: float = 0.0
    e2e_latency_ms_p99: float = 0.0
    status: str = "skipped: offline by default"
    note: str = (
        "Set CW_BENCH_LLM_PROVIDER + CW_BENCH_LLM_API_KEY and pass "
        "--with-real-model to enable.  See benchmarks/README.md."
    )
    per_call_results: list[dict[str, object]] = field(default_factory=list)


def _run_e2e_real_model(
    gold: list[dict[str, object]],
    *,
    enabled: bool,
    sample_limit: int = 5,
) -> E2ERealModelStats:
    """Optionally run a tiny end-to-end real-model benchmark (issue #269).

    Returns immediately with a ``"skipped: ..."`` status when disabled or
    when required env vars are missing.  When fully wired, performs a
    small sample (≤ *sample_limit* gold queries) of one-shot completions
    against an OpenAI-compatible HTTP endpoint, recording prompt /
    completion token usage and round-trip latency.

    The HTTP layer uses :mod:`urllib.request` so the harness does not need
    a new third-party SDK dependency.
    """
    if not enabled:
        return E2ERealModelStats(status="skipped: offline by default")

    provider = os.environ.get("CW_BENCH_LLM_PROVIDER", "").strip()
    api_key = os.environ.get("CW_BENCH_LLM_API_KEY", "").strip()
    if not provider or not api_key:
        return E2ERealModelStats(
            status="skipped: missing CW_BENCH_LLM_PROVIDER/CW_BENCH_LLM_API_KEY env vars"
        )

    model_id = os.environ.get("CW_BENCH_LLM_MODEL", "").strip() or "gpt-4o-mini"
    endpoint = os.environ.get(
        "CW_BENCH_LLM_ENDPOINT", "https://api.openai.com/v1/chat/completions"
    ).strip()

    # Lazy imports so module load stays light when E2E is off.
    import json as _json  # noqa: PLC0415
    import urllib.error  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    samples = gold[:sample_limit]
    latencies: list[float] = []
    prompt_total = 0
    completion_total = 0
    per_call: list[dict[str, object]] = []

    for entry in samples:
        query = str(entry.get("query", ""))
        payload = _json.dumps(
            {
                "model": model_id,
                "messages": [{"role": "user", "content": query}],
                "max_tokens": 64,
                "temperature": 0.0,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=30) as resp:
                body = _json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return E2ERealModelStats(
                provider=provider,
                model=model_id,
                samples=len(per_call),
                status=f"skipped: transport error ({type(exc).__name__})",
            )
        latency_ms = (time.perf_counter() - t0) * 1000

        usage = body.get("usage") or {}
        prompt_n = int(usage.get("prompt_tokens", 0))
        completion_n = int(usage.get("completion_tokens", 0))
        prompt_total += prompt_n
        completion_total += completion_n
        latencies.append(latency_ms)
        per_call.append(
            {
                "query": query[:64],
                "prompt_tokens": prompt_n,
                "completion_tokens": completion_n,
                "latency_ms": round(latency_ms, 2),
            }
        )

    latencies.sort()
    cost = _estimate_usd_cost(provider, model_id, prompt_total, completion_total)
    return E2ERealModelStats(
        provider=provider,
        model=model_id,
        samples=len(per_call),
        prompt_tokens_total=prompt_total,
        completion_tokens_total=completion_total,
        estimated_usd_cost=round(cost, 4),
        e2e_latency_ms_p50=_percentile(latencies, 0.50),
        e2e_latency_ms_p95=_percentile(latencies, 0.95),
        e2e_latency_ms_p99=_percentile(latencies, 0.99),
        status="ok",
        per_call_results=per_call,
    )


# Per-model USD prices ($ per 1M tokens).  Kept intentionally small — the
# scorecard's E2E section is a sanity-check, not a pricing source of truth.
_E2E_USD_PRICES: dict[tuple[str, str], tuple[float, float]] = {
    ("openai", "gpt-4o-mini"): (0.15, 0.60),
    ("openai", "gpt-4o"): (2.50, 10.00),
    ("openai", "gpt-3.5-turbo"): (0.50, 1.50),
}


def _estimate_usd_cost(
    provider: str, model: str, prompt_tokens: int, completion_tokens: int
) -> float:
    """Estimate USD cost for an end-to-end run; returns ``0.0`` if unknown.

    The intent is a directional cost signal, not an invoice — unknown
    (provider, model) pairs return ``0.0`` and the renderer surfaces this
    as ``"unpriced"``.
    """
    rates = _E2E_USD_PRICES.get((provider.lower(), model.lower()))
    if rates is None:
        return 0.0
    prompt_rate, completion_rate = rates
    return (prompt_tokens / 1_000_000) * prompt_rate + (
        completion_tokens / 1_000_000
    ) * completion_rate


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


_DEFAULT_MATRIX_BACKENDS = "tfidf,bm25,fuzzy,embedding_hashing,embedding_st"
_SUPPORTED_BACKENDS = frozenset({"tfidf", "bm25", "fuzzy", "embedding_hashing", "embedding_st"})
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
            "Comma-separated routing backends for the matrix run (any of: "
            "tfidf, bm25, fuzzy, embedding_hashing, embedding_st). "
            "'fuzzy' requires the [retrieval] extra; 'embedding_st' requires "
            "the [embeddings] extra; missing backends are recorded with an "
            "explicit 'status: skipped' row rather than silently omitted."
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
    parser.add_argument(
        "--mixed-shapes",
        action="store_true",
        help=(
            "Additionally run the matrix against the head-heavy + long-tail "
            "mixed-namespace catalog at size 500 (issue #277).  Adds the "
            "'routing_matrix_mixed_shape' block to the JSON output."
        ),
    )
    parser.add_argument(
        "--no-tiktoken-parity",
        action="store_true",
        help=(
            "Disable the CharDivFourEstimator vs cl100k_base parity check "
            "(issue #268).  Default is enabled because tiktoken is a core "
            "dep; the parity block is small and additive."
        ),
    )
    parser.add_argument(
        "--with-real-model",
        action="store_true",
        help=(
            "Run the optional end-to-end real-model cost / latency capture "
            "(issue #269).  Off by default.  Requires CW_BENCH_LLM_PROVIDER + "
            "CW_BENCH_LLM_API_KEY env vars; otherwise emits a 'skipped' row."
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

    # Mixed-namespace head-heavy + long-tail matrix (issue #277).
    mixed_shape_cells: list[MatrixCell] = []
    if args.mixed_shapes:
        backends_for_mixed = _csv_str_list(args.backends) or _csv_str_list(_DEFAULT_MATRIX_BACKENDS)
        mixed_shape_cells = _run_matrix_mixed_shape(
            gold=gold,
            backends=backends_for_mixed,
            k=args.k,
            seed=args.seed,
            n_timing_runs=args.timing_runs,
        )
        if mixed_shape_cells:
            _print_matrix_table(mixed_shape_cells, args.k)

    # Tiktoken parity check (issue #268).  Default is enabled; offline CIs
    # without the cached encoding will see a 'skipped' status row instead.
    parity_stats: TiktokenParityStats | None = None
    if not args.no_tiktoken_parity:
        parity_stats = _run_tiktoken_parity(gold)

    # Optional end-to-end real-model capture (issue #269).
    e2e_stats = _run_e2e_real_model(gold, enabled=args.with_real_model)

    out: dict[str, object] = {
        "benchmark_version": "1.2",
        "k": args.k,
        "seed": args.seed,
        "environment": asdict(_capture_environment()),
        "reference_rig": _REFERENCE_RIG,
        "routing": [asdict(r) for r in routing_results],
        "context": context_dicts,
    }
    if matrix_cells:
        out["routing_matrix"] = [asdict(c) for c in matrix_cells]
    if ns_cells:
        out["routing_per_namespace"] = [asdict(c) for c in ns_cells]
    if mixed_shape_cells:
        out["routing_matrix_mixed_shape"] = [asdict(c) for c in mixed_shape_cells]
    if parity_stats is not None:
        out["tiktoken_parity"] = asdict(parity_stats)
    # E2E stats: always emit (the 'skipped' default documents the opt-in).
    out["e2e_real_model"] = asdict(e2e_stats)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nResults written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Routing-scale profiler and bottleneck report (issue #684).

Profiles the deterministic routing path across catalog sizes up to 10k tools
and isolates *where* cold-start latency goes, then quantifies the win from
persisting the routing graph (``save_graph`` / ``load_graph``) and the fitted
retriever index (:class:`RoutingIndexCache`, issues #543 / #624 / #685).

For each catalog size it measures:

* ``build_ms``            — :meth:`TreeBuilder.build` (graph construction).
* ``cold_start_ms``       — build the graph + construct a router + first
  :meth:`Router.route` (the from-scratch cost; includes the one-time
  retriever ``fit``).
* ``warm_start_ms``       — :func:`load_graph` from disk + construct a router
  whose :class:`CachedRetriever` loads the fitted index from a warmed on-disk
  cache + first ``route``; both the graph build and the fit are skipped.
* ``warm_route_p50_ms``   — steady-state per-query latency once warm.
* ``cold_speedup_x``      — ``cold_start_ms / warm_start_ms``.
* ``graph_bytes`` / ``index_bytes`` — persisted artifact sizes on disk.

Deterministic inputs (seeded catalog, fixed queries).  No LLM calls, no
network.  Latency is wall-clock and host-dependent; the *relative* numbers
(build share, cold speedup) are the portable signal.

Usage::

    python benchmarks/routing_scale.py
    python benchmarks/routing_scale.py --sizes 100,1000,10000 --runs 7
    python benchmarks/routing_scale.py --no-report   # JSON only
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from contextweaver.routing.graph_io import load_graph, save_graph  # noqa: E402
from contextweaver.routing.index_cache import (  # noqa: E402
    CachedRetriever,
    RoutingIndexCache,
)
from contextweaver.routing.registry import TfIdfRetriever  # noqa: E402
from contextweaver.routing.router import Router  # noqa: E402
from contextweaver.routing.tree import TreeBuilder  # noqa: E402
from contextweaver.types import SelectableItem  # noqa: E402

_RESULTS = _ROOT / "benchmarks" / "results" / "routing_scale.json"
_REPORT = _ROOT / "docs" / "benchmarks" / "routing-scale.md"
_DEFAULT_SIZES = [100, 1000, 5000, 10000]
_QUERIES = [
    "search service 03 module 07 records",
    "create a new entry for module 02",
    "list operations for service 05",
    "export module 09 audit data",
    "update service 01 configuration",
]
_ACTIONS = ("search", "list", "create", "update", "export", "delete")


def make_scaling_catalog(n: int, *, branching: int = 12) -> list[SelectableItem]:
    """Return a deterministic *n*-item catalog with multi-level namespaces.

    Items are spread over a ``svcNN.modNN`` namespace grid so the tree builder
    has real hierarchical structure to group on (the realistic shape of a
    large gateway catalog), with descriptive text/tags giving the retriever
    lexical signal.

    Args:
        n: Number of items.
        branching: Distinct services and modules per level.

    Returns:
        A sorted list of :class:`SelectableItem`.
    """
    items: list[SelectableItem] = []
    for i in range(n):
        svc = i % branching
        mod = (i // branching) % branching
        action = _ACTIONS[i % len(_ACTIONS)]
        namespace = f"svc{svc:02d}.mod{mod:02d}"
        items.append(
            SelectableItem(
                f"{namespace}.tool_{i:05d}",
                "tool",
                f"{action}_svc{svc:02d}_mod{mod:02d}_{i}",
                f"{action} operation {i} for service {svc} module {mod}",
                tags=[f"svc{svc:02d}", f"mod{mod:02d}", action],
                namespace=namespace,
            )
        )
    return sorted(items, key=lambda it: it.id)


def _percentile(sorted_samples: list[float], pct: float) -> float:
    if not sorted_samples:
        return 0.0
    idx = min(int(len(sorted_samples) * pct), len(sorted_samples) - 1)
    return round(sorted_samples[idx], 4)


def _time_ms(fn) -> float:  # type: ignore[no-untyped-def]
    start = time.perf_counter()
    fn()
    return (time.perf_counter() - start) * 1000.0


@dataclass
class ScaleRow:
    """Profiling result for one catalog size."""

    catalog_size: int
    build_ms: float
    cold_start_ms: float
    warm_start_ms: float
    warm_route_p50_ms: float
    warm_route_p95_ms: float
    cold_speedup_x: float
    graph_bytes: int
    index_bytes: int


def _cold_start(items: list[SelectableItem]) -> float:
    """Time a full from-scratch route: build graph + router + first route."""

    def run() -> None:
        graph = TreeBuilder().build(items)
        Router(graph, items=items, retriever=TfIdfRetriever()).route(_QUERIES[0])

    return _time_ms(run)


def _profile_size(n: int, seed: int, runs: int) -> ScaleRow:
    items = make_scaling_catalog(n)

    # Time one build and keep the graph for the (untimed) warm-start setup.
    start = time.perf_counter()
    graph = TreeBuilder().build(items)
    build_ms = (time.perf_counter() - start) * 1000.0
    cold_start_ms = _cold_start(items)

    work_dir = Path(tempfile.mkdtemp(prefix="cw_scale_"))
    graph_path = work_dir / "graph.json"
    cache_dir = work_dir / "index"

    # Setup (not timed): persist the graph and warm the on-disk index cache.
    save_graph(graph, graph_path)
    Router(
        graph, items=items, retriever=CachedRetriever(TfIdfRetriever(), RoutingIndexCache(cache_dir))
    ).route(_QUERIES[0])

    # Warm start (timed): load the graph + load the fitted index, then route.
    def warm_run() -> None:
        loaded = load_graph(graph_path)
        retriever = CachedRetriever(TfIdfRetriever(), RoutingIndexCache(cache_dir))
        Router(loaded, items=items, retriever=retriever).route(_QUERIES[0])

    warm_start_ms = _time_ms(warm_run)

    # Steady-state per-query latency on a warmed router.
    warm_router = Router(
        graph, items=items, retriever=CachedRetriever(TfIdfRetriever(), RoutingIndexCache(cache_dir))
    )
    warm_router.route(_QUERIES[0])
    # Sample one representative query a few times — enough for a steady-state
    # p50/p95 without paying N×|queries| route calls (per-query routing is
    # itself super-linear at scale, so a 25-call sweep would dominate runtime).
    samples = sorted(_time_ms(lambda: warm_router.route(_QUERIES[0])) for _ in range(runs))

    graph_bytes = graph_path.stat().st_size
    index_files = list(cache_dir.glob("idx_*.json"))
    index_bytes = index_files[0].stat().st_size if index_files else 0
    speedup = cold_start_ms / warm_start_ms if warm_start_ms > 0 else 0.0

    # Don't leak the per-size scratch dir (graph + index artifacts).
    shutil.rmtree(work_dir, ignore_errors=True)

    return ScaleRow(
        catalog_size=n,
        build_ms=round(build_ms, 4),
        cold_start_ms=round(cold_start_ms, 4),
        warm_start_ms=round(warm_start_ms, 4),
        warm_route_p50_ms=_percentile(samples, 0.50),
        warm_route_p95_ms=_percentile(samples, 0.95),
        cold_speedup_x=round(speedup, 2),
        graph_bytes=graph_bytes,
        index_bytes=index_bytes,
    )


def _render_report(rows: list[ScaleRow], env: dict[str, object]) -> str:
    lines = [
        "# Routing-scale profile and bottleneck report",
        "",
        "<!-- Generated by `make benchmark-routing-scale` (benchmarks/routing_scale.py)."
        "  Do not edit by hand. -->",
        "",
        "Profiles the deterministic routing path across catalog sizes up to 10k "
        "tools (issue #684) and the persistent graph + fitted-index caches "
        "(issues #543 / #624 / #685).",
        "",
        f"Measured on: {env['platform']} · Python {env['python_version']} · "
        f"{env['cpu_logical_cores']} logical cores.  Absolute latency is "
        "host-dependent; the **build_ms** column and the **cold speedup** are the "
        "portable signal.",
        "",
        "| catalog | build ms | cold start ms | warm start ms | warm route p50 ms | "
        "cold speedup | graph bytes | index bytes |",
        "|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for r in rows:
        lines.append(
            f"| {r.catalog_size} | {r.build_ms:.1f} | {r.cold_start_ms:.1f} | "
            f"{r.warm_start_ms:.1f} | {r.warm_route_p50_ms:.3f} | "
            f"{r.cold_speedup_x:.1f}× | {r.graph_bytes} | {r.index_bytes} |"
        )
    lines += [
        "",
        "## Bottleneck",
        "",
        "Graph construction (`TreeBuilder.build`) dominates **cold start** and "
        "grows **super-linearly** with catalog size — see the `build_ms` column.  "
        "The one-time retriever fit is comparatively cheap.  Per-query routing "
        "latency (`warm route p50`) also grows super-linearly and is itself "
        "significant at 10k, but that is a separate cost from cold start.  The "
        "cost this work targets is the *repeated* cold start (graph build + "
        "index fit) paid by deployments that re-create routers over the same "
        "catalog: a process per request, a worker pool, a CLI in a loop.",
        "",
        "## Optimization",
        "",
        "Persisting both derived artifacts removes that repeated work: the graph "
        "via `save_graph` / `load_graph` and the fitted index via "
        "`RoutingIndexCache` + `CachedRetriever`.  A warm start loads both from "
        "disk and skips the build and the fit entirely (`cold speedup` column).  "
        "Warm loads are byte-identical to a cold fit, so routing quality and "
        "determinism are unchanged "
        "(`tests/test_routing_quality_guardrails.py`).",
        "",
        "## Caveats",
        "",
        "* The cache shortcuts cold start, **not** per-query latency: the "
        "`warm route p50` column is unaffected by it.  `TreeBuilder.build`'s "
        "super-linear build cost and the super-linear per-query routing cost are "
        "separate, pre-existing scaling characteristics — this work mitigates "
        "the *repeated* build+fit via persistence rather than changing the "
        "(determinism- and quality-sensitive) build or beam-search algorithms.  "
        "Reducing those first-pass costs is tracked separately.",
        "",
    ]
    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="contextweaver routing-scale profiler (#684)")
    parser.add_argument(
        "--sizes",
        default=",".join(str(s) for s in _DEFAULT_SIZES),
        help="Comma-separated catalog sizes to profile.",
    )
    parser.add_argument("--seed", type=int, default=42, help="Catalog RNG seed (reserved).")
    parser.add_argument(
        "--runs", type=int, default=5, help="Repetitions per query for warm latency."
    )
    parser.add_argument("--output", default=str(_RESULTS), help="JSON output path.")
    parser.add_argument(
        "--no-report", action="store_true", help="Skip writing the Markdown report."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the routing-scale profile and write JSON (+ Markdown report)."""
    args = _parse_args(argv)
    sizes = [int(p) for p in args.sizes.split(",") if p.strip()]

    env = {
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "cpu_logical_cores": os.cpu_count() or 0,
    }
    print(f"Profiling routing at sizes {sizes} (runs={args.runs})", flush=True)

    rows: list[ScaleRow] = []
    for n in sizes:
        row = _profile_size(n, seed=args.seed, runs=args.runs)
        rows.append(row)
        print(
            f"  n={row.catalog_size:>6}  build={row.build_ms:9.1f}ms  "
            f"cold_start={row.cold_start_ms:9.1f}ms  warm_start={row.warm_start_ms:8.1f}ms  "
            f"speedup={row.cold_speedup_x:6.1f}x",
            flush=True,
        )

    out = {
        "benchmark_version": "1.0",
        "seed": args.seed,
        "environment": env,
        "routing_scale": [asdict(r) for r in rows],
    }
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nResults written to {out_path}", flush=True)

    if not args.no_report:
        _REPORT.parent.mkdir(parents=True, exist_ok=True)
        _REPORT.write_text(_render_report(rows, env), encoding="utf-8")
        print(f"Report written to {_REPORT}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

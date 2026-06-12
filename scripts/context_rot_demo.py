#!/usr/bin/env python3
"""Deterministic "context rot" demo: tool-routing quality vs catalog size (#349).

The repo's central claim is *more context ≠ better answers*. This script makes
that concrete **without a live model**, so it is reproducible and can run in
CI. It grows a tool catalog with near-duplicate distractor variants (keeping
the full natural tool pool present so the evaluated query set stays fixed at
``benchmarks/routing_gold.json``) and measures, at each size:

- ``naive_visible_tools`` — how many tool descriptions a *naive* "dump every
  schema" route prompt would carry (equals the catalog size).
- ``contextweaver_visible_tools`` — how many ``ChoiceCard``s contextweaver's
  router actually surfaces (bounded by ``top_k``, so flat as the catalog
  grows).
- ``recall_at_5`` — whether the correct tool survives into that bounded
  shortlist. This is the deterministic stand-in for "answer quality": as
  irrelevant tools pile up, lexical routing recall erodes — visible *context
  rot* — while the model-visible surface stays bounded.

This is intentionally the *routing-visibility* proxy, not a live-model
answer-accuracy measurement. The end-to-end, real-model variant lives in the
optional, credential-gated notebook ``notebooks/context_rot_live.ipynb`` and is
tracked toward the public quality+cost benchmark in issue #345.

Two artifacts are committed and kept in sync:

- ``benchmarks/results/context_rot.json`` — the computed curve (source of
  truth for rendering, mirroring how ``benchmarks/results/latest.json`` feeds
  ``scripts/render_scorecard.py``).
- ``docs/assets/context_rot.svg`` — the rendered chart embedded in the README
  and ``docs/context_rot.md``.

Determinism contract: given the same committed JSON, the rendered SVG is
byte-identical on every run and platform. CI verifies the committed pair with::

    python scripts/context_rot_demo.py --check

Usage::

    python scripts/context_rot_demo.py            # recompute JSON + render SVG
    python scripts/context_rot_demo.py --check     # render from JSON; fail on SVG drift
    python scripts/context_rot_demo.py --json-only  # recompute JSON only

Unlike ``render_scorecard.py`` this script imports contextweaver to compute the
curve, so it runs after ``pip install -e .`` (the same posture as
``benchmarks/benchmark.py``). The ``--check`` path renders from the committed
JSON and never recomputes, so the gate stays portable across the CI matrix; the
compute path is exercised by ``tests/test_context_rot_demo.py``.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from _golden import check_text_artifacts

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_JSON = REPO_ROOT / "benchmarks" / "results" / "context_rot.json"
DEFAULT_SVG = REPO_ROOT / "docs" / "assets" / "context_rot.svg"
DEFAULT_GOLD = REPO_ROOT / "benchmarks" / "routing_gold.json"

DEMO_VERSION = "1.0"
SEED = 42
TOP_K = 5
# Geometric growth, starting at the full natural pool (83 tools) so every gold
# query stays reachable and ``queries_evaluated`` is constant across sizes.
CATALOG_SIZES: tuple[int, ...] = (83, 166, 332, 664, 1328)


# ---------------------------------------------------------------------------
# Computation (imports contextweaver; not used by --check)
# ---------------------------------------------------------------------------


def _grow_catalog(natural: list[Any], n: int) -> list[Any]:
    """Return *n* items: the full *natural* pool plus seeded distractor variants.

    Variants reuse each original's tags/namespace (preserving routing-signal
    density) but carry distinct ids, so they never match a gold query — they
    are pure distractors. Mirrors ``benchmarks/benchmark.py:_make_catalog`` so
    the demo and the benchmark grow catalogs the same way.
    """
    from contextweaver.types import SelectableItem

    if n <= len(natural):
        return sorted(natural, key=lambda i: i.id)[:n]
    items: list[Any] = list(natural)
    version = 2
    while len(items) < n:
        for orig in list(natural):
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


def compute_curve(
    sizes: Sequence[int] = CATALOG_SIZES,
    *,
    seed: int = SEED,
    top_k: int = TOP_K,
    gold_path: Path = DEFAULT_GOLD,
) -> dict[str, Any]:
    """Compute the context-rot curve and return a JSON-serialisable payload.

    Deterministic: identical inputs yield identical numbers on every run.
    """
    from contextweaver.eval import EvalDataset, evaluate_routing
    from contextweaver.routing.catalog import generate_sample_catalog, load_catalog_dicts
    from contextweaver.routing.router import Router
    from contextweaver.routing.tree import TreeBuilder

    dataset = EvalDataset.load(gold_path)
    # The full natural pool (capped generator => the whole pool). Keeping it
    # present in every catalog holds the evaluated query set constant.
    natural = sorted(
        load_catalog_dicts(generate_sample_catalog(n=10_000, seed=seed)),
        key=lambda i: i.id,
    )
    natural_ids = {item.id for item in natural}

    points: list[dict[str, Any]] = []
    for n in sizes:
        items = _grow_catalog(natural, n)
        router = Router(TreeBuilder().build(items), items=items, top_k=top_k)
        report = evaluate_routing(router, dataset, catalog_ids=natural_ids)
        points.append(
            {
                "catalog_size": n,
                "queries_evaluated": report.queries_evaluated,
                "naive_visible_tools": n,
                "contextweaver_visible_tools": report.avg_candidates,
                "recall_at_1": report.top_1_recall,
                "recall_at_3": report.top_3_recall,
                "recall_at_5": report.top_5_recall,
                "mrr": report.mrr,
            }
        )

    return {
        "demo_version": DEMO_VERSION,
        "seed": seed,
        "top_k": top_k,
        "natural_pool_size": len(natural),
        "gold_cases": len(dataset),
        "points": points,
    }


# ---------------------------------------------------------------------------
# Rendering (pure, stdlib-only — drives the --check gate)
# ---------------------------------------------------------------------------

_W = 720
_H = 470
_PLOT_X0 = 80
_PLOT_X1 = 690


def _x_positions(count: int) -> list[float]:
    """Evenly spaced x centres for *count* categorical points."""
    if count == 1:
        return [(_PLOT_X0 + _PLOT_X1) / 2]
    step = (_PLOT_X1 - _PLOT_X0) / (count - 1)
    return [_PLOT_X0 + step * i for i in range(count)]


def _polyline(xs: list[float], ys: list[float], colour: str, *, dash: bool = False) -> str:
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys, strict=True))
    dash_attr = ' stroke-dasharray="6 4"' if dash else ""
    return (
        f'<polyline fill="none" stroke="{colour}" stroke-width="2.5"{dash_attr} points="{pts}" />'
    )


def _dots(xs: list[float], ys: list[float], colour: str) -> str:
    return "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{colour}" />'
        for x, y in zip(xs, ys, strict=True)
    )


def _panel(
    *,
    top: float,
    height: float,
    title: str,
    x_centres: list[float],
    series: list[tuple[str, str, list[float], bool]],
    y_to_px: Callable[[float, float, float], float],
    y_ticks: list[tuple[float, str]],
) -> list[str]:
    """Render one chart panel. *series* is (label, colour, values, dashed)."""
    bottom = top + height
    x0, x1 = _PLOT_X0, _PLOT_X1
    out: list[str] = [
        f'<text x="{x0}" y="{top - 10:.0f}" class="ptitle">{title}</text>',
        f'<line x1="{x0}" y1="{bottom:.0f}" x2="{x1}" y2="{bottom:.0f}" class="axis" />',
        f'<line x1="{x0}" y1="{top:.0f}" x2="{x0}" y2="{bottom:.0f}" class="axis" />',
    ]
    for value, label in y_ticks:
        y = y_to_px(value, top, bottom)
        out.append(
            f'<line x1="{_PLOT_X0}" y1="{y:.1f}" x2="{_PLOT_X1}" y2="{y:.1f}" class="grid" />'
        )
        out.append(f'<text x="{_PLOT_X0 - 8:.0f}" y="{y + 4:.1f}" class="ytick">{label}</text>')
    for _label, colour, values, dashed in series:
        ys = [y_to_px(v, top, bottom) for v in values]
        out.append(_polyline(x_centres, ys, colour, dash=dashed))
        out.append(_dots(x_centres, ys, colour))
    return out


def render_svg(payload: dict[str, Any]) -> str:
    """Render the committed curve *payload* to a deterministic SVG string."""
    points = payload["points"]
    sizes = [p["catalog_size"] for p in points]
    naive = [float(p["naive_visible_tools"]) for p in points]
    cw_tools = [float(p["contextweaver_visible_tools"]) for p in points]
    recall = [float(p["recall_at_5"]) for p in points]
    x_centres = _x_positions(len(points))

    max_tools = max(naive + cw_tools + [10.0])

    def _log_y(value: float, top: float, bottom: float) -> float:
        # log10 scale from 1 .. max_tools (tool counts span 5 .. ~1328).
        hi = math.log10(max_tools)
        frac = math.log10(max(value, 1.0)) / hi if hi > 0 else 0.0
        return bottom - frac * (bottom - top)

    def _lin_y(value: float, top: float, bottom: float) -> float:
        return bottom - max(0.0, min(value, 1.0)) * (bottom - top)

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_W}" height="{_H}" '
        f'viewBox="0 0 {_W} {_H}" font-family="-apple-system,Segoe UI,Roboto,sans-serif">',
        "<style>"
        ".axis{stroke:#94a3b8;stroke-width:1}"
        ".grid{stroke:#e2e8f0;stroke-width:1}"
        ".title{font-size:18px;font-weight:700;fill:#0f172a}"
        ".sub{font-size:12px;fill:#475569}"
        ".ptitle{font-size:13px;font-weight:600;fill:#0f172a}"
        ".ytick{font-size:10px;fill:#64748b;text-anchor:end}"
        ".xtick{font-size:10px;fill:#64748b;text-anchor:middle}"
        ".legend{font-size:11px;fill:#334155}"
        "</style>",
        f'<rect width="{_W}" height="{_H}" fill="#ffffff" />',
        f'<text x="{_PLOT_X0}" y="28" class="title">'
        "Context rot: more tools ≠ better routing</text>",
        f'<text x="{_PLOT_X0}" y="46" class="sub">'
        f"Gold set: {payload['gold_cases']} queries · top_k={payload['top_k']} · "
        f"seed={payload['seed']} · deterministic (no live model)</text>",
    ]

    # Legend, placed inside Panel A's upper-right so it never collides with
    # the title row.
    lx = _PLOT_X1 - 235
    parts.append(f'<rect x="{lx:.0f}" y="151" width="14" height="3" fill="#dc2626" />')
    parts.append(f'<text x="{lx + 18:.0f}" y="158" class="legend">naive (all tool schemas)</text>')
    parts.append(f'<rect x="{lx:.0f}" y="167" width="14" height="3" fill="#2563eb" />')
    parts.append(
        f'<text x="{lx + 18:.0f}" y="174" class="legend">contextweaver (ChoiceCards)</text>'
    )

    # Panel A — model-visible tools (log scale).
    parts.extend(
        _panel(
            top=78,
            height=150,
            title="Tools visible to the model per route prompt (log scale)",
            x_centres=x_centres,
            series=[
                ("naive", "#dc2626", naive, False),
                ("contextweaver", "#2563eb", cw_tools, False),
            ],
            y_to_px=_log_y,
            y_ticks=[(5, "5"), (50, "50"), (500, "500"), (max_tools, f"{int(max_tools)}")],
        )
    )

    # Panel B — contextweaver correct-tool recall@5 (linear 0..1).
    parts.extend(
        _panel(
            top=300,
            height=140,
            title="contextweaver correct-tool recall@5 (right tool in the shortlist)",
            x_centres=x_centres,
            series=[("recall@5", "#2563eb", recall, False)],
            y_to_px=_lin_y,
            y_ticks=[(0.0, "0%"), (0.25, "25%"), (0.5, "50%"), (1.0, "100%")],
        )
    )

    # Shared x-axis labels.
    for x, n in zip(x_centres, sizes, strict=True):
        parts.append(f'<text x="{x:.1f}" y="458" class="xtick">{n}</text>')
    parts.append(
        f'<text x="{(_PLOT_X0 + _PLOT_X1) / 2:.0f}" y="468" class="sub" '
        f'text-anchor="middle">catalog size (tools)</text>'
    )

    parts.append("</svg>")
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", default=str(DEFAULT_JSON), help="curve JSON path")
    parser.add_argument("--svg", default=str(DEFAULT_SVG), help="rendered SVG path")
    parser.add_argument(
        "--check",
        action="store_true",
        help="render the committed JSON and fail on SVG drift (does not recompute)",
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="recompute and write the curve JSON only (skip SVG)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Render or check the context-rot demo. Returns 0 on success, 1 on drift."""
    args = _parse_args(argv)
    json_path = Path(args.json)
    svg_path = Path(args.svg)

    if args.check:
        if not json_path.exists():
            print(f"error: {json_path} not found — run `make context-rot`.", file=sys.stderr)
            return 1
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        rendered = {svg_path: render_svg(payload)}
        return check_text_artifacts(rendered, label="context-rot", regen="make context-rot")

    payload = compute_curve()
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {json_path}")
    if not args.json_only:
        svg_path.parent.mkdir(parents=True, exist_ok=True)
        svg_path.write_text(render_svg(payload), encoding="utf-8")
        print(f"Wrote {svg_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

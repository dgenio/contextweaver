#!/usr/bin/env python3
"""Gate benchmark quality regressions with tolerance bands (issue #491).

Companion to ``scripts/benchmark_delta.py`` (the informational sticky PR
comment). Where the delta script *describes* head-vs-base movement, this
script *enforces* it: a PR that regresses a gated quality metric beyond its
band — recall@k, MRR, precision@k, token-savings — exits non-zero so CI can
block the merge. Latency cells are never gated (runner variance).

The gate compares a head ``latest.json`` against the committed base
``latest.json`` cell-by-cell, keyed by identity (catalog size, backend×size,
or scenario), so a regression in any one cell is surfaced with its location.

The script is **stdlib-only on the hot path**; the YAML config is parsed with a
lazy ``yaml`` import (a core dependency) only when ``--gating-config`` is read,
so the import is paid in CI after ``pip install -e .`` runs.

Usage::

    python scripts/benchmark_gate.py --base base.json --head head.json
    python scripts/benchmark_gate.py --base base.json --head head.json \\
        --gating-config benchmarks/gating.yaml
    python scripts/benchmark_gate.py --base base.json --head head.json --override

Exit codes: ``0`` when every gated cell is within band (or ``--override`` is
set), ``1`` when any gated cell regresses beyond its band, ``2`` on bad input.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GATING_CONFIG = REPO_ROOT / "benchmarks" / "gating.yaml"

# Fraction metrics live on a 0..1 scale, so a band expressed in percentage
# points (pp) is applied as ``band / 100``. Percent metrics are already 0..100
# and the band is applied directly. Anything not listed here is informational.
_FRACTION_METRICS = ("recall_at_k", "mrr", "precision_at_k")
_PERCENT_METRICS = ("token_savings_pct",)

# Mirrors benchmarks/gating.yaml so the gate has a safe default when no config
# file is present (e.g. a partial checkout). Kept in sync with that file.
DEFAULT_BANDS: dict[str, float] = {
    "recall_at_k": 1.0,
    "mrr": 1.0,
    "precision_at_k": 1.0,
    "token_savings_pct": 2.0,
}


@dataclass(frozen=True)
class GateViolation:
    """One gated cell that regressed beyond its tolerance band."""

    metric: str
    cell: str
    base: float
    head: float
    regression_pp: float
    band_pp: float

    def describe(self) -> str:
        """Return a single-line, deterministic human-readable summary."""
        return (
            f"{self.metric} [{self.cell}]: {self.base:.4f} -> {self.head:.4f} "
            f"(-{self.regression_pp:.2f}pp, band {self.band_pp:.2f}pp)"
        )


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GatingConfig:
    """Resolved gate configuration: per-metric bands plus the override label."""

    bands: dict[str, float]
    override_label: str = "benchmark-accepted"


def load_gating_config(path: Path | None) -> GatingConfig:
    """Load bands from *path*; fall back to :data:`DEFAULT_BANDS` when absent.

    Only the ``quality`` metrics whose band is a positive number are gated;
    a metric set to ``gating: false`` (or omitted) is treated as informational.
    """
    if path is None or not path.exists():
        return GatingConfig(bands=dict(DEFAULT_BANDS))
    import yaml  # lazy: keeps the import off the no-config path

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    quality = raw.get("quality", {}) if isinstance(raw, dict) else {}
    bands: dict[str, float] = {}
    for metric, spec in (quality or {}).items():
        if not isinstance(spec, dict):
            continue
        band = spec.get("max_regression_pp")
        if isinstance(band, (int, float)) and band >= 0:
            bands[str(metric)] = float(band)
    override = str(raw.get("override_label", "benchmark-accepted")) if isinstance(raw, dict) else ""
    return GatingConfig(bands=bands or dict(DEFAULT_BANDS), override_label=override)


# ---------------------------------------------------------------------------
# Cell extraction — each gated cell is (metric, cell-label, value)
# ---------------------------------------------------------------------------


def _routing_cells(payload: dict[str, Any]) -> dict[tuple[str, str], float]:
    """Gated metrics from the single-backend ``routing`` summary rows."""
    cells: dict[tuple[str, str], float] = {}
    for row in payload.get("routing", []):
        if not isinstance(row, dict):
            continue
        size = int(row.get("catalog_size", 0))
        for metric in _FRACTION_METRICS:
            if metric in row:
                cells[(metric, f"routing/size={size}")] = float(row[metric])
    return cells


def _matrix_cells(payload: dict[str, Any]) -> dict[tuple[str, str], float]:
    """Gated metrics from ``routing_matrix`` cells; skip non-``ok`` cells."""
    cells: dict[tuple[str, str], float] = {}
    for row in payload.get("routing_matrix", []):
        if not isinstance(row, dict) or str(row.get("status", "ok")) != "ok":
            continue
        backend = str(row.get("backend", ""))
        size = int(row.get("catalog_size", 0))
        for metric in _FRACTION_METRICS:
            if metric in row:
                cells[(metric, f"matrix/{backend}@{size}")] = float(row[metric])
    return cells


def _token_savings_cells(payload: dict[str, Any]) -> dict[tuple[str, str], float]:
    """Token-savings cells from each context row's ``naive_delta`` block."""
    cells: dict[tuple[str, str], float] = {}
    for row in payload.get("context", []):
        if not isinstance(row, dict):
            continue
        nd = row.get("naive_delta")
        if isinstance(nd, dict) and "pct_reduction" in nd:
            scenario = str(row.get("scenario", ""))
            cells[("token_savings_pct", f"context/{scenario}")] = float(nd["pct_reduction"])
    return cells


def _all_cells(payload: dict[str, Any]) -> dict[tuple[str, str], float]:
    cells: dict[tuple[str, str], float] = {}
    cells.update(_routing_cells(payload))
    cells.update(_matrix_cells(payload))
    cells.update(_token_savings_cells(payload))
    return cells


def _regression_pp(metric: str, base: float, head: float) -> float:
    """Return the regression in percentage points (positive = got worse)."""
    drop = base - head
    if metric in _PERCENT_METRICS:
        return drop  # already on a 0..100 scale
    return drop * 100.0  # fraction (0..1) -> pp


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


def evaluate_gate(
    base: dict[str, Any], head: dict[str, Any], config: GatingConfig
) -> list[GateViolation]:
    """Return every gated cell in *head* that regressed beyond its band vs *base*.

    Cells absent from *base* (new cells) cannot regress and are skipped. Cells
    present in *base* but absent from *head* are also skipped — a removed cell is
    a structural change, not a quality regression, and is surfaced by the delta
    comment, not the gate. Results are sorted for deterministic output.
    """
    base_cells = _all_cells(base)
    head_cells = _all_cells(head)
    violations: list[GateViolation] = []
    for key, head_value in head_cells.items():
        metric, cell = key
        band = config.bands.get(metric)
        if band is None or key not in base_cells:
            continue
        base_value = base_cells[key]
        regression = _regression_pp(metric, base_value, head_value)
        if regression > band:
            violations.append(
                GateViolation(
                    metric=metric,
                    cell=cell,
                    base=base_value,
                    head=head_value,
                    regression_pp=regression,
                    band_pp=band,
                )
            )
    return sorted(violations, key=lambda v: (v.metric, v.cell))


def render_report(violations: list[GateViolation], *, overridden: bool) -> str:
    """Render a deterministic plain-text gate report."""
    if not violations:
        return "benchmark gate: PASS — all gated quality metrics within band."
    lines = [f"benchmark gate: {len(violations)} metric(s) regressed beyond band:"]
    lines.extend(f"  - {v.describe()}" for v in violations)
    if overridden:
        lines.append("")
        lines.append("Override label present — downgrading failure to a warning.")
    else:
        lines.append("")
        lines.append(
            "Fix the regression, or apply the override label with a rationale in "
            "the PR description to accept it."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else None,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--base", required=True, help="Baseline (committed) latest.json")
    parser.add_argument("--head", required=True, help="PR head latest.json")
    parser.add_argument(
        "--gating-config",
        default=str(DEFAULT_GATING_CONFIG),
        help="Path to gating.yaml (bands + override label)",
    )
    parser.add_argument(
        "--override",
        action="store_true",
        help="Downgrade any gate failure to a warning (exit 0).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        base = json.loads(Path(args.base).read_text(encoding="utf-8"))
        head = json.loads(Path(args.head).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"error: malformed JSON: {exc}", file=sys.stderr)
        return 2

    config = load_gating_config(Path(args.gating_config) if args.gating_config else None)
    violations = evaluate_gate(base, head, config)
    print(render_report(violations, overridden=args.override))
    if violations and not args.override:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

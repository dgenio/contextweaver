"""Metric extraction and comparison helpers for ``benchmark_gate.py``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_FRACTION_METRICS = ("recall_at_k", "mrr", "precision_at_k")
_PERCENT_METRICS = ("token_savings_pct",)
_RELATIVE_PERCENT_METRICS = ("avg_compaction_ratio",)


@dataclass(frozen=True)
class GateViolation:
    """One gated cell that regressed or disappeared."""

    metric: str
    cell: str
    base: float
    head: float | None
    regression: float | None
    band: float
    unit: str

    def describe(self) -> str:
        """Return a deterministic human-readable summary."""
        if self.head is None:
            return f"{self.metric} [{self.cell}]: base {self.base:.4f} -> missing from head"
        return (
            f"{self.metric} [{self.cell}]: {self.base:.4f} -> {self.head:.4f} "
            f"(-{self.regression:.2f}{self.unit}, band {self.band:.2f}{self.unit})"
        )


def band_key(metric: str) -> str:
    """Return the configuration key used by *metric*."""
    if metric in _RELATIVE_PERCENT_METRICS:
        return "max_regression_pct"
    return "max_regression_pp"


def _routing_cells(payload: dict[str, Any]) -> dict[tuple[str, str], float]:
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


def _context_cells(payload: dict[str, Any]) -> dict[tuple[str, str], float]:
    cells: dict[tuple[str, str], float] = {}
    for row in payload.get("context", []):
        if not isinstance(row, dict):
            continue
        scenario = str(row.get("scenario", ""))
        nd = row.get("naive_delta")
        if isinstance(nd, dict) and "pct_reduction" in nd:
            cells[("token_savings_pct", f"context/{scenario}")] = float(nd["pct_reduction"])
        if "avg_compaction_ratio" in row:
            cells[("avg_compaction_ratio", f"context/{scenario}")] = float(
                row["avg_compaction_ratio"]
            )
    return cells


def _all_cells(payload: dict[str, Any]) -> dict[tuple[str, str], float]:
    cells: dict[tuple[str, str], float] = {}
    cells.update(_routing_cells(payload))
    cells.update(_matrix_cells(payload))
    cells.update(_context_cells(payload))
    return cells


def _regression(metric: str, base: float, head: float) -> tuple[float, str]:
    drop = base - head
    if metric in _PERCENT_METRICS:
        return drop, "pp"
    if metric in _RELATIVE_PERCENT_METRICS:
        return ((drop / abs(base)) * 100.0 if base else 0.0), "%"
    return drop * 100.0, "pp"


def band_unit(metric: str) -> str:
    """Return the report unit for *metric*'s configured band."""
    return "%" if metric in _RELATIVE_PERCENT_METRICS else "pp"


def evaluate_gate(
    base: dict[str, Any], head: dict[str, Any], bands: dict[str, float]
) -> list[GateViolation]:
    """Return gated regressions, including base cells missing from *head*."""
    base_cells = _all_cells(base)
    head_cells = _all_cells(head)
    violations: list[GateViolation] = []
    for key, base_value in base_cells.items():
        metric, cell = key
        band = bands.get(metric)
        if band is None:
            continue
        if key not in head_cells:
            violations.append(
                GateViolation(metric, cell, base_value, None, None, band, band_unit(metric))
            )
            continue
        head_value = head_cells[key]
        regression, unit = _regression(metric, base_value, head_value)
        if regression > band:
            violations.append(
                GateViolation(metric, cell, base_value, head_value, regression, band, unit)
            )
    return sorted(violations, key=lambda violation: (violation.metric, violation.cell))

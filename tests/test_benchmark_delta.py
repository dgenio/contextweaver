"""Regression tests for scripts/benchmark_delta.py.

Covers the matrix-delta path that needs to handle ``status``-bearing
("skipped") cells from `benchmarks/benchmark.py --matrix` without
treating their zeroed metrics as a real accuracy/latency regression
(would emit false-positive ⚠️ markers on every PR).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from benchmark_delta import _render_matrix_section  # noqa: E402


def _payload(matrix_rows: list[dict[str, object]]) -> dict[str, object]:
    return {"routing_matrix": matrix_rows}


def test_matrix_delta_renders_skipped_row_for_status_bearing_cell() -> None:
    """A skipped cell on head renders as ``_skipped_`` with the reason, not a regression."""
    base = _payload(
        [
            {
                "backend": "fuzzy",
                "catalog_size": 100,
                "recall_at_k": 0.45,
                "mrr": 0.3,
                "latency_ms_p99": 1.0,
            }
        ]
    )
    head = _payload(
        [
            {
                "backend": "fuzzy",
                "catalog_size": 100,
                "recall_at_k": 0.0,
                "mrr": 0.0,
                "latency_ms_p99": 0.0,
                "status": "skipped: rapidfuzz not installed",
            }
        ]
    )
    out = _render_matrix_section(base, head)
    assert "_skipped_" in out
    assert "skipped: rapidfuzz not installed" in out
    # No ⚠️ — the zeroed metrics must not be reported as a regression.
    assert "⚠️" not in out


def test_matrix_delta_ok_cells_render_metrics() -> None:
    """Cells with the canonical ``status="ok"`` must render full metrics.

    Regression: an earlier version of `_render_matrix_section` checked
    `if head_status or base_status`, which matched every real cell
    (``MatrixCell.status`` defaults to ``"ok"``) and suppressed every
    metric row. The benchmark-delta bot comment on PR #235 caught this
    in production. The gate is now `status != "ok"`, aligned with
    `scripts/render_scorecard.py`.
    """
    base = _payload(
        [
            {
                "backend": "tfidf",
                "catalog_size": 100,
                "recall_at_k": 0.5,
                "mrr": 0.4,
                "latency_ms_p99": 1.0,
                "status": "ok",
            }
        ]
    )
    head = _payload(
        [
            {
                "backend": "tfidf",
                "catalog_size": 100,
                "recall_at_k": 0.5,
                "mrr": 0.4,
                "latency_ms_p99": 1.0,
                "status": "ok",
            }
        ]
    )
    out = _render_matrix_section(base, head)
    # Numeric markers present; no skipped marker for "ok" cells.
    assert "_skipped_" not in out
    assert "0.5000" in out


def test_matrix_delta_missing_status_treated_as_ok() -> None:
    """Backwards-compatibility: a cell with no `status` field still renders metrics."""
    base = _payload(
        [
            {
                "backend": "bm25",
                "catalog_size": 100,
                "recall_at_k": 0.3,
                "mrr": 0.2,
                "latency_ms_p99": 5.0,
            }
        ]
    )
    head = _payload(
        [
            {
                "backend": "bm25",
                "catalog_size": 100,
                "recall_at_k": 0.3,
                "mrr": 0.2,
                "latency_ms_p99": 5.0,
            }
        ]
    )
    out = _render_matrix_section(base, head)
    assert "_skipped_" not in out
    assert "0.3000" in out


def test_matrix_delta_empty_payload_returns_empty_string() -> None:
    """No matrix rows in either side → no section emitted."""
    assert _render_matrix_section({}, {}) == ""

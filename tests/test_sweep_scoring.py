"""Tests for the ScoringConfig sweep tool (#214)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import sweep_scoring  # noqa: E402

from contextweaver.config import ScoringConfig


def test_grid_size_is_243() -> None:
    """3 × 3 × 3 × 3 × 3 = 243 — the spec calls for ≥ 81."""
    expected = (
        len(sweep_scoring._RECENCY_WEIGHTS)
        * len(sweep_scoring._TAG_MATCH_WEIGHTS)
        * len(sweep_scoring._KIND_PRIORITY_WEIGHTS)
        * len(sweep_scoring._TOKEN_COST_PENALTIES)
        * len(sweep_scoring._DEDUP_THRESHOLDS)
    )
    assert expected == 243
    assert expected >= 81


def test_default_is_in_grid() -> None:
    """The current ScoringConfig defaults must appear as one cell in the grid.

    If this fails, the grid no longer contains the default and the report's
    "default rank" line cannot be rendered — so it's a wire-up bug, not a
    test ambiguity.
    """
    default = ScoringConfig()
    assert default.recency_weight in sweep_scoring._RECENCY_WEIGHTS
    assert default.tag_match_weight in sweep_scoring._TAG_MATCH_WEIGHTS
    assert default.kind_priority_weight in sweep_scoring._KIND_PRIORITY_WEIGHTS
    assert default.token_cost_penalty in sweep_scoring._TOKEN_COST_PENALTIES
    assert default.dedup_threshold in sweep_scoring._DEDUP_THRESHOLDS


def test_composite_weights_sum_to_one() -> None:
    """Composite weights (0.50 + 0.30 + 0.20) sum to 1.0 — guards against drift."""
    assert sweep_scoring._composite(100.0, 0.0, 0.0) == 100.0
    assert sweep_scoring._composite(0.0, 0.0, 0.0) == 50.0  # 0.30*100 + 0.20*100
    assert sweep_scoring._composite(100.0, 100.0, 100.0) == 50.0  # 0.5*100 + 0 + 0


def test_pareto_identification() -> None:
    """A row that's better-or-equal on every axis (strict on at least one) is reported."""
    default_tup = sweep_scoring.WeightTuple(0.3, 0.25, 0.35, 0.10, 0.85)
    default_row = sweep_scoring.SweepRow(
        tuple_=default_tup,
        coverage_pct_avg=50.0,
        util_overrun_avg=10.0,
        drop_rate_avg=5.0,
        composite=50.0,
    )
    better = sweep_scoring.SweepRow(
        tuple_=sweep_scoring.WeightTuple(0.4, 0.25, 0.35, 0.10, 0.85),
        coverage_pct_avg=60.0,  # strictly better
        util_overrun_avg=10.0,
        drop_rate_avg=5.0,
        composite=55.0,
    )
    worse = sweep_scoring.SweepRow(
        tuple_=sweep_scoring.WeightTuple(0.2, 0.25, 0.35, 0.10, 0.85),
        coverage_pct_avg=40.0,  # worse
        util_overrun_avg=10.0,
        drop_rate_avg=5.0,
        composite=45.0,
    )
    equal = sweep_scoring.SweepRow(
        tuple_=sweep_scoring.WeightTuple(0.4, 0.35, 0.35, 0.10, 0.85),
        coverage_pct_avg=50.0,  # equal — not strict
        util_overrun_avg=10.0,
        drop_rate_avg=5.0,
        composite=50.0,
    )
    dominators = sweep_scoring._pareto_dominators(default_row, [default_row, better, worse, equal])
    assert better in dominators
    assert worse not in dominators
    assert equal not in dominators


def test_report_renders_top_10_and_default() -> None:
    """A minimal in-memory row set produces a report mentioning the default and grid size."""
    rows = [
        sweep_scoring.SweepRow(
            tuple_=sweep_scoring.WeightTuple(0.3, 0.25, 0.35, 0.10, 0.85),
            coverage_pct_avg=90.0,
            util_overrun_avg=1.0,
            drop_rate_avg=0.5,
            composite=95.0,
        ),
    ]
    text = sweep_scoring.render_report(rows)
    assert "Composite formula" in text
    assert "**default**" in text  # the row is the default and ranks #1


def test_render_report_is_deterministic() -> None:
    """Two render_report calls on identical rows produce byte-identical markdown."""
    rows = [
        sweep_scoring.SweepRow(
            tuple_=sweep_scoring.WeightTuple(0.3, 0.25, 0.35, 0.10, 0.85),
            coverage_pct_avg=90.0,
            util_overrun_avg=1.0,
            drop_rate_avg=0.5,
            composite=95.0,
        ),
        sweep_scoring.SweepRow(
            tuple_=sweep_scoring.WeightTuple(0.4, 0.15, 0.25, 0.05, 0.80),
            coverage_pct_avg=85.0,
            util_overrun_avg=2.0,
            drop_rate_avg=1.0,
            composite=92.0,
        ),
    ]
    assert sweep_scoring.render_report(rows) == sweep_scoring.render_report(rows)

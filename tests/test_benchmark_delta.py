"""Tests for the benchmark delta renderer (#211).

The script lives under ``scripts/`` so it can run before the package is
installed. Tests reach it via ``sys.path`` the same way
:mod:`tests.test_render_scorecard` does.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import benchmark_delta  # noqa: E402


def _make_payload(
    *,
    recall: float = 0.5,
    p99: float = 10.0,
    dropped: int = 0,
) -> dict[str, object]:
    """Build a minimal latest.json shape with one row each for routing/context/matrix."""
    return {
        "benchmark_version": "1.1",
        "k": 5,
        "seed": 42,
        "routing": [
            {
                "catalog_size": 100,
                "queries_evaluated": 50,
                "precision_at_k": 0.1,
                "recall_at_k": recall,
                "mrr": 0.3,
                "latency_ms_p50": 1.0,
                "latency_ms_p95": 5.0,
                "latency_ms_p99": p99,
            }
        ],
        "context": [
            {
                "scenario": "test",
                "event_count": 10,
                "items_included": 9,
                "items_dropped": dropped,
                "dedup_removed": 0,
                "prompt_tokens": 500,
                "budget_tokens": 6000,
                "budget_utilization_pct": 8.3,
                "artifacts_created": 2,
                "avg_compaction_ratio": 1.0,
            }
        ],
        "matrix": [
            {
                "backend": "tfidf",
                "catalog_size": 100,
                "queries_evaluated": 50,
                "precision_at_k": 0.1,
                "recall_at_k": recall,
                "mrr": 0.3,
                "latency_ms_p50": 1.0,
                "latency_ms_p95": 5.0,
                "latency_ms_p99": p99,
                "status": "",
            }
        ],
    }


def test_zero_delta_no_warning_markers() -> None:
    """Identical inputs produce all-✅ markers and no negative deltas."""
    payload = _make_payload()
    out = benchmark_delta.render(payload, payload)
    assert "⚠️" not in out
    # Every numeric Δ should be +0.0000 / +0.000 (exact, no rounding).
    assert "+0.0000" in out
    # The marker comment must appear so sticky-comment finds it.
    assert benchmark_delta.COMMENT_MARKER in out


def test_latency_warn_marker_fires_at_30pct_overshoot() -> None:
    """⚠️ fires only when head exceeds base × 1.30."""
    base = _make_payload(p99=10.0)
    # 13.01 ms > 10.0 × 1.30 = 13.0 → should warn.
    head = _make_payload(p99=13.01)
    out = benchmark_delta.render(base, head)
    assert "⚠️" in out


def test_latency_warn_marker_silent_below_threshold() -> None:
    """At exactly base × 1.30, no warning."""
    base = _make_payload(p99=10.0)
    head = _make_payload(p99=13.0)  # exactly at threshold
    out = benchmark_delta.render(base, head)
    assert "⚠️" not in out


def test_recall_negative_delta_signed() -> None:
    """Recall regression shows a negative signed delta (no leading +)."""
    base = _make_payload(recall=0.50)
    head = _make_payload(recall=0.45)
    out = benchmark_delta.render(base, head)
    # The signed format prefixes "+" for non-negative deltas and "-" only
    # from the float itself for negative — so the line should carry
    # "-0.0500" verbatim.
    assert "-0.0500" in out
    # And the positive recall change shouldn't appear.
    assert "+0.0500" not in out


def test_zero_baseline_p99_no_warn() -> None:
    """A zero base_p99 (no baseline) must never fire ⚠️."""
    base = _make_payload(p99=0.0)
    head = _make_payload(p99=100.0)
    out = benchmark_delta.render(base, head)
    assert "⚠️" not in out


def test_idempotent_render() -> None:
    """Calling render twice on the same payloads produces byte-identical output."""
    base = _make_payload(recall=0.4, p99=5.0)
    head = _make_payload(recall=0.5, p99=7.0)
    a = benchmark_delta.render(base, head)
    b = benchmark_delta.render(base, head)
    assert a == b


def test_missing_base_file(tmp_path: Path) -> None:
    """When --base does not exist, the script writes a 'no baseline' notice and exits 0."""
    head = _make_payload()
    head_path = tmp_path / "head.json"
    head_path.write_text(json.dumps(head), encoding="utf-8")
    base_path = tmp_path / "does-not-exist.json"
    out_path = tmp_path / "delta.md"
    rc = benchmark_delta.main(
        ["--base", str(base_path), "--head", str(head_path), "--output", str(out_path)]
    )
    assert rc == 0
    text = out_path.read_text(encoding="utf-8")
    assert "No baseline" in text
    assert benchmark_delta.COMMENT_MARKER in text


def test_missing_head_file(tmp_path: Path) -> None:
    """A missing --head must exit non-zero (the script can't render anything)."""
    out_path = tmp_path / "delta.md"
    rc = benchmark_delta.main(
        [
            "--base",
            str(tmp_path / "base.json"),
            "--head",
            str(tmp_path / "missing.json"),
            "--output",
            str(out_path),
        ]
    )
    assert rc == 1


def test_latency_threshold_matches_scorecard_renderer() -> None:
    """The +30% budget must stay in sync with the scorecard renderer."""
    # If this fails, the scorecard's ⚠️ rule and the PR delta's ⚠️ rule
    # have diverged — bug, not a test ambiguity.
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "render_scorecard",
        Path(__file__).parent.parent / "scripts" / "render_scorecard.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert pytest.approx(mod._LATENCY_BUDGET_MULTIPLIER) == (
        benchmark_delta.LATENCY_BUDGET_MULTIPLIER
    )


def test_missing_matrix_block_skipped_cleanly() -> None:
    """A payload without a matrix array still renders (matrix section omitted)."""
    base = _make_payload()
    head = _make_payload()
    base.pop("matrix")
    head.pop("matrix")
    out = benchmark_delta.render(base, head)
    assert "### Matrix" not in out
    # Routing + context tables still present.
    assert "### Routing" in out
    assert "### Context pipeline" in out

"""Unit tests for benchmark metric helper functions.

These are pure-function tests for the correctness foundation of the benchmark
harness.  Import via sys.path since benchmarks/ is outside src/.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import benchmark  # noqa: E402  (import after sys.path manipulation)
from benchmark import (  # noqa: E402
    MatrixCell,
    _backend_available,
    _make_catalog,
    _percentile,
    _precision_at_k,
    _recall_at_k,
    _reciprocal_rank,
    _run_matrix_benchmark,
)

# Synthetic variant IDs always end with .vN (e.g. billing.charge_customer.v2).
# Natural IDs never match this pattern (billing.invoices.void contains .v but not .vN at end).
_SYNTHETIC_PAT = re.compile(r"[.]v[0-9]+\Z")


def test_precision_at_k() -> None:
    assert _precision_at_k(["a", "b", "c"], ["b"], k=3) == pytest.approx(1 / 3)


def test_precision_at_k_zero_k() -> None:
    assert _precision_at_k(["a"], ["a"], k=0) == 0.0


def test_recall_at_k_full() -> None:
    assert _recall_at_k(["a", "b"], ["a", "b"], k=2) == 1.0


def test_recall_at_k_partial() -> None:
    assert _recall_at_k(["a", "b", "c"], ["a", "d"], k=3) == pytest.approx(0.5)


def test_recall_at_k_empty_expected() -> None:
    assert _recall_at_k(["a"], [], k=1) == 1.0


def test_reciprocal_rank_first_hit() -> None:
    assert _reciprocal_rank(["a", "b"], ["a"]) == 1.0


def test_reciprocal_rank_second_hit() -> None:
    assert _reciprocal_rank(["a", "b"], ["b"]) == pytest.approx(0.5)


def test_reciprocal_rank_no_hit() -> None:
    assert _reciprocal_rank(["a", "b"], ["c"]) == 0.0


def test_make_catalog_natural_pool_no_synthetic() -> None:
    """83-item catalog must be the full natural pool with no synthetic variants."""
    items = _make_catalog(83)
    assert all(not _SYNTHETIC_PAT.search(item.id) for item in items)
    assert len(items) == 83


def test_make_catalog_size_50() -> None:
    items = _make_catalog(50)
    assert len(items) == 50
    assert all(not _SYNTHETIC_PAT.search(item.id) for item in items)


def test_make_catalog_size_1000_has_synthetic() -> None:
    items = _make_catalog(1000)
    assert len(items) == 1000
    assert any(_SYNTHETIC_PAT.search(item.id) for item in items)


# ---------------------------------------------------------------------------
# Matrix mode (#208)
# ---------------------------------------------------------------------------


def test_percentile_empty_returns_zero() -> None:
    assert _percentile([], 0.5) == 0.0


def test_percentile_ordering() -> None:
    """p50 / p95 land on the expected indices for a known input."""
    values = list(range(100))
    assert _percentile(values, 0.50) == 50.0
    assert _percentile(values, 0.95) == 95.0
    assert _percentile(values, 0.99) == 99.0


def test_backend_available_tfidf_and_bm25() -> None:
    ok, reason = _backend_available("tfidf")
    assert ok is True and reason == ""
    ok, reason = _backend_available("bm25")
    assert ok is True and reason == ""


def test_backend_available_fuzzy_skip_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fuzzy is reported as skipped when FuzzyScorer is None."""
    monkeypatch.setattr(benchmark, "FuzzyScorer", None)
    ok, reason = _backend_available("fuzzy")
    assert ok is False
    assert "rapidfuzz" in reason


def test_run_matrix_skips_fuzzy_with_status_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing fuzzy backend produces skip cells with non-empty ``status``."""
    monkeypatch.setattr(benchmark, "FuzzyScorer", None)
    gold = [
        {
            "query": "list dashboards",
            "expected": ["analytics.dashboards.list"],
            "tags": ["analytics", "dashboards"],
            "namespace": "analytics",
        }
    ]
    cells, _ns = _run_matrix_benchmark(
        gold=gold,
        backends=["tfidf", "fuzzy"],
        catalog_sizes=[100],
        k=5,
        seed=42,
        n_timing_runs=1,
    )
    by_backend = {(c.backend, c.catalog_size): c for c in cells}
    fuzzy_cell = by_backend[("fuzzy", 100)]
    assert fuzzy_cell.status.startswith("skipped:")
    # Skip cells must carry zero metrics — never silently report fake numbers.
    assert fuzzy_cell.recall_at_k == 0.0
    assert fuzzy_cell.queries_evaluated == 0
    tfidf_cell = by_backend[("tfidf", 100)]
    assert tfidf_cell.status == ""


def test_run_matrix_per_namespace_keyed_by_backend() -> None:
    """Per-namespace recall is captured at the largest size, keyed by backend."""
    gold = [
        {
            "query": "list dashboards",
            "expected": ["analytics.dashboards.list"],
            "tags": ["analytics", "dashboards"],
            "namespace": "analytics",
        },
        {
            "query": "export audit log",
            "expected": ["admin.audit.export"],
            "tags": ["admin", "audit"],
            "namespace": "admin",
        },
    ]
    _cells, per_ns = _run_matrix_benchmark(
        gold=gold,
        backends=["tfidf"],
        catalog_sizes=[100, 200],
        k=5,
        seed=42,
        n_timing_runs=1,
    )
    assert "tfidf" in per_ns
    # Both namespaces with gold queries appear (sorted in render layer).
    assert set(per_ns["tfidf"].keys()) == {"admin", "analytics"}


def test_matrix_cell_shape() -> None:
    """MatrixCell carries all required fields, status defaults to empty."""
    cell = MatrixCell(
        backend="tfidf",
        catalog_size=100,
        queries_evaluated=50,
        precision_at_k=0.1,
        recall_at_k=0.5,
        mrr=0.3,
        latency_ms_p50=0.5,
        latency_ms_p95=0.8,
        latency_ms_p99=1.0,
    )
    assert cell.status == ""


def test_parse_args_rejects_unknown_backends() -> None:
    """Typos in `--backends` exit cleanly with code 2, not a traceback."""
    with pytest.raises(SystemExit) as exc_info:
        benchmark._parse_args(["--matrix", "--backends", "tfidf,bogus"])
    assert exc_info.value.code == 2


def test_parse_args_accepts_supported_backends() -> None:
    """All three documented backends pass `--backends` validation."""
    args = benchmark._parse_args(["--matrix", "--backends", "tfidf,bm25,fuzzy"])
    assert args.backends == ["tfidf", "bm25", "fuzzy"]

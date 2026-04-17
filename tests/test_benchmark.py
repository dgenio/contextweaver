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

from benchmark import _make_catalog, _precision_at_k, _recall_at_k, _reciprocal_rank

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

"""Tests for the canonical routing metrics (issue #354).

These pin the exact math of the shared helpers in
:mod:`contextweaver.eval.metrics` and guard against regressions in the
consolidation that unified ``benchmarks/benchmark.py`` and
``contextweaver.eval.routing`` onto one definition of each metric.
"""

from __future__ import annotations

import pytest

from contextweaver.eval import precision_at_k, recall_at_k, reciprocal_rank

# ------------------------------------------------------------------
# recall_at_k — classic fractional recall
# ------------------------------------------------------------------


def test_recall_at_k_single_expected_is_hit_rate() -> None:
    # With one expected id, fractional recall collapses to a 0/1 hit.
    predicted = ["a", "b", "c", "d", "e"]
    assert recall_at_k(predicted, ["c"], 1) == 0.0
    assert recall_at_k(predicted, ["c"], 3) == 1.0
    assert recall_at_k(predicted, ["c"], 5) == 1.0


def test_recall_at_k_multi_expected_is_fraction() -> None:
    # The behaviour that distinguishes fractional recall from a boolean
    # hit-rate: two expected ids, only one inside top-k -> 0.5.
    predicted = ["a", "b", "c", "d"]
    assert recall_at_k(predicted, ["a", "z"], 2) == 0.5
    assert recall_at_k(predicted, ["a", "b"], 2) == 1.0
    assert recall_at_k(predicted, ["y", "z"], 2) == 0.0


def test_recall_at_k_empty_expected_is_one() -> None:
    assert recall_at_k(["a"], [], 3) == 1.0


def test_recall_at_k_dedupes_expected() -> None:
    # Duplicate expected ids must not inflate the denominator.
    assert recall_at_k(["a", "b"], ["a", "a"], 2) == 1.0


def test_recall_at_k_matches_historical_benchmark_formula() -> None:
    # Regression guard: for the unique-expected shape the benchmark fixtures
    # use (routing_gold.json never repeats an expected id within a case), the
    # canonical recall equals the exact formula ``benchmarks/benchmark.py`` used
    # before #354, so scorecard numbers are unchanged by the consolidation. The
    # canonical version additionally dedupes ``expected`` (see
    # ``test_recall_at_k_dedupes_expected``) — an intentional hardening that
    # only diverges from the legacy formula on the non-benchmark duplicate case,
    # so every case below uses unique expected ids.
    def _legacy_recall(predicted: list[str], expected: list[str], k: int) -> float:
        if not expected:
            return 1.0
        hits = sum(1 for e in expected if e in set(predicted[:k]))
        return hits / len(expected)

    cases = [
        (["a", "b", "c"], ["c"], 1),
        (["a", "b", "c"], ["c"], 3),
        (["a", "b", "c", "d"], ["a", "d"], 2),
        (["a", "b", "c", "d"], ["a", "d", "z"], 4),
        (["a"], [], 2),
    ]
    for predicted, expected, k in cases:
        assert recall_at_k(predicted, expected, k) == _legacy_recall(predicted, expected, k)


# ------------------------------------------------------------------
# precision_at_k
# ------------------------------------------------------------------


def test_precision_at_k() -> None:
    predicted = ["a", "b", "c", "d"]
    assert precision_at_k(predicted, ["a", "c"], 4) == 0.5
    assert precision_at_k(predicted, ["a"], 1) == 1.0
    assert precision_at_k(predicted, ["z"], 4) == 0.0


def test_precision_at_k_zero_k() -> None:
    assert precision_at_k(["a"], ["a"], 0) == 0.0


# ------------------------------------------------------------------
# reciprocal_rank
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("predicted", "expected", "want"),
    [
        (["a", "b", "c"], ["a"], 1.0),
        (["a", "b", "c"], ["b"], 0.5),
        (["a", "b", "c"], ["c"], 1.0 / 3.0),
        (["a", "b", "c"], ["z"], 0.0),
        (["a", "b", "c"], ["b", "c"], 0.5),  # first match wins
    ],
)
def test_reciprocal_rank(predicted: list[str], expected: list[str], want: float) -> None:
    assert reciprocal_rank(predicted, expected) == want

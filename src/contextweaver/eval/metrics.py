"""Canonical routing-quality metrics (issue #354).

Single source of truth for the rank-based routing metrics shared by the
library evaluation harness (:mod:`contextweaver.eval.routing`) and the
standalone benchmark script (``benchmarks/benchmark.py``).  Before this
module the two implementations defined the *same names for different
semantics* — ``benchmark.py`` used a fractional ``recall@k`` while the
eval harness used a boolean "hit-rate@k" — which could silently drift.

The metrics here are deliberately small, pure, and dependency-free so the
benchmark script can import them without pulling the rest of the package.

Definitions
-----------
- :func:`recall_at_k` — classic recall@k, the **fraction** of expected ids
  recovered within the top-*k* predictions (``|expected ∩ top-k| / |expected|``).
  This is the single canonical recall definition; the former boolean
  "hit-rate" reading has been collapsed into it (issue #354).  For the
  common single-expected-id case the two are numerically identical.
- :func:`precision_at_k` — fraction of the top-*k* predictions that are
  expected (``hits / k``).
- :func:`reciprocal_rank` — reciprocal of the 1-based rank of the first
  expected id (``0.0`` when none appear).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

__all__ = ["precision_at_k", "recall_at_k", "reciprocal_rank"]


def recall_at_k(predicted: Sequence[str], expected: Collection[str], k: int) -> float:
    """Classic recall@k: fraction of *expected* ids within the top-*k* predictions.

    Args:
        predicted: Ranked prediction ids (most relevant first).
        expected: The gold-standard ids a correct router should surface.
        k: Rank cutoff (``predicted`` is sliced to its first *k* entries).

    Returns:
        ``|expected ∩ predicted[:k]| / |expected|`` in ``[0.0, 1.0]``.  An
        empty *expected* set returns ``1.0`` (vacuously satisfied); callers
        that treat empty-expected cases as unevaluable filter them out
        before calling.
    """
    expected_set = set(expected)
    if not expected_set:
        return 1.0
    top_k = set(predicted[:k])
    hits = sum(1 for e in expected_set if e in top_k)
    return hits / len(expected_set)


def precision_at_k(predicted: Sequence[str], expected: Collection[str], k: int) -> float:
    """Precision@k: fraction of the top-*k* predictions that are *expected*.

    The denominator is always *k* (classic precision@k), so a router that
    returns fewer than *k* candidates is scored against the full cutoff
    rather than its actual output length. This matches the historical
    ``benchmarks/benchmark.py`` definition, so the published scorecard is
    unaffected by the #354 consolidation.

    Returns ``0.0`` when ``k <= 0``.
    """
    if k <= 0:
        return 0.0
    expected_set = set(expected)
    hits = sum(1 for p in predicted[:k] if p in expected_set)
    return hits / k


def reciprocal_rank(predicted: Sequence[str], expected: Collection[str]) -> float:
    """Reciprocal rank of the first *expected* id in *predicted* (``0.0`` if none)."""
    expected_set = set(expected)
    for rank, pid in enumerate(predicted, start=1):
        if pid in expected_set:
            return 1.0 / rank
    return 0.0

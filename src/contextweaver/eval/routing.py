"""Routing evaluation harness (issue #12).

:func:`evaluate_routing` runs every :class:`~contextweaver.eval.dataset.EvalCase`
through a :class:`~contextweaver.routing.router.Router` and aggregates
quality metrics into a :class:`RoutingEvalReport`:

- **top-1 / top-3 / top-5 recall** — mean classic recall@k across cases:
  the fraction of each case's expected tool ids recovered within the
  top-*k* candidates.  For the common single-expected-id case this equals
  the share of cases with an expected id in the top-*k*.
- **MRR** — mean reciprocal rank of the first expected id across cases.
- **avg candidates** — mean number of candidates returned per query.
- **avg confidence gap** — mean ``score[0] - score[1]`` (0.0 when fewer
  than two candidates), a proxy for how decisive routing was.
- **avg beam steps** — mean number of beam-search expansion steps
  (captured via ``debug=True``), a proxy for routing work.

Recall and MRR come from :mod:`contextweaver.eval.metrics`, the single
source of truth shared with ``benchmarks/benchmark.py`` (issue #354), so
the library harness and the benchmark script can no longer drift.  Routing
is deterministic, so the report is reproducible for a given router/dataset.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any

from contextweaver.eval.dataset import EvalDataset
from contextweaver.eval.metrics import recall_at_k, reciprocal_rank
from contextweaver.routing.router import Router

__all__ = ["RoutingEvalReport", "evaluate_routing"]

# Rank cutoffs reported by every routing evaluation, per issue #12.
_RECALL_KS: tuple[int, ...] = (1, 3, 5)


@dataclass
class RoutingEvalReport:
    """Aggregated routing-quality metrics for a dataset."""

    queries_evaluated: int = 0
    queries_skipped: int = 0
    top_1_recall: float = 0.0
    top_3_recall: float = 0.0
    top_5_recall: float = 0.0
    mrr: float = 0.0
    avg_candidates: float = 0.0
    avg_confidence_gap: float = 0.0
    avg_beam_steps: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "queries_evaluated": self.queries_evaluated,
            "queries_skipped": self.queries_skipped,
            "top_1_recall": self.top_1_recall,
            "top_3_recall": self.top_3_recall,
            "top_5_recall": self.top_5_recall,
            "mrr": self.mrr,
            "avg_candidates": self.avg_candidates,
            "avg_confidence_gap": self.avg_confidence_gap,
            "avg_beam_steps": self.avg_beam_steps,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoutingEvalReport:
        """Build a :class:`RoutingEvalReport` from a raw dict."""
        return cls(
            queries_evaluated=int(data.get("queries_evaluated", 0)),
            queries_skipped=int(data.get("queries_skipped", 0)),
            top_1_recall=float(data.get("top_1_recall", 0.0)),
            top_3_recall=float(data.get("top_3_recall", 0.0)),
            top_5_recall=float(data.get("top_5_recall", 0.0)),
            mrr=float(data.get("mrr", 0.0)),
            avg_candidates=float(data.get("avg_candidates", 0.0)),
            avg_confidence_gap=float(data.get("avg_confidence_gap", 0.0)),
            avg_beam_steps=float(data.get("avg_beam_steps", 0.0)),
        )

    def summary(self) -> str:
        """Return a compact, human-readable one-block summary."""
        return (
            f"Routing eval: {self.queries_evaluated} evaluated, "
            f"{self.queries_skipped} skipped\n"
            f"  recall@1={self.top_1_recall:.4f}  "
            f"recall@3={self.top_3_recall:.4f}  "
            f"recall@5={self.top_5_recall:.4f}\n"
            f"  mrr={self.mrr:.4f}  "
            f"avg_candidates={self.avg_candidates:.2f}  "
            f"avg_confidence_gap={self.avg_confidence_gap:.4f}  "
            f"avg_beam_steps={self.avg_beam_steps:.2f}"
        )


def evaluate_routing(
    router: Router,
    dataset: EvalDataset,
    *,
    catalog_ids: set[str] | None = None,
) -> RoutingEvalReport:
    """Evaluate *router* against *dataset* and return a :class:`RoutingEvalReport`.

    Args:
        router: A constructed router to route every case through.
        dataset: The gold-standard cases.
        catalog_ids: Optional set of ids actually present in the router's
            catalog.  When provided, each case's ``expected`` ids are
            intersected with it and cases left with no reachable expected
            id are skipped (counted in ``queries_skipped``).  This keeps
            recall meaningful when a dataset references tools a smaller
            catalog does not contain.

    Returns:
        Aggregated metrics.  All averages are over *evaluated* cases only.
    """
    recalls: dict[int, list[float]] = {k: [] for k in _RECALL_KS}
    rrs: list[float] = []
    candidate_counts: list[int] = []
    confidence_gaps: list[float] = []
    beam_steps: list[int] = []
    skipped = 0

    for case in dataset.cases:
        expected = set(case.expected)
        if catalog_ids is not None:
            expected &= catalog_ids
        if not expected:
            skipped += 1
            continue

        result = router.route(case.query, debug=True)
        predicted = result.candidate_ids

        for k in _RECALL_KS:
            recalls[k].append(recall_at_k(predicted, expected, k))
        rrs.append(reciprocal_rank(predicted, expected))
        candidate_counts.append(len(predicted))
        gap = result.scores[0] - result.scores[1] if len(result.scores) >= 2 else 0.0
        confidence_gaps.append(gap)
        beam_steps.append(len(result.trace.steps))

    evaluated = len(rrs)

    def _mean(values: list[float]) -> float:
        return round(statistics.mean(values), 4) if values else 0.0

    return RoutingEvalReport(
        queries_evaluated=evaluated,
        queries_skipped=skipped,
        top_1_recall=_mean(recalls[1]),
        top_3_recall=_mean(recalls[3]),
        top_5_recall=_mean(recalls[5]),
        mrr=_mean(rrs),
        avg_candidates=round(statistics.mean(candidate_counts), 2) if candidate_counts else 0.0,
        avg_confidence_gap=_mean(confidence_gaps),
        avg_beam_steps=round(statistics.mean(beam_steps), 2) if beam_steps else 0.0,
    )

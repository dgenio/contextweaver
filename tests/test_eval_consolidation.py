"""Tests for the consolidation quality evaluation harness (issue #683)."""

from __future__ import annotations

from contextweaver.context.consolidation_types import (
    ConsolidationReport,
    EpisodeCluster,
    PromotedFact,
)
from contextweaver.eval.consolidation import ConsolidationEvalReport, evaluate_consolidation


def _fact(text: str) -> PromotedFact:
    return PromotedFact(
        fact_id=f"fact:consolidated:{abs(hash(text)) % 9999:04d}", key="c", text=text
    )


def _report(*texts: str, clusters: int = 0) -> ConsolidationReport:
    return ConsolidationReport(
        clusters=[EpisodeCluster(cluster_id=f"cluster_{i:03d}") for i in range(clusters)],
        promoted=[_fact(t) for t in texts],
    )


def test_precision_and_coverage_with_gold() -> None:
    report = _report("Customer prefers email", "Build fails on staging")
    ev = evaluate_consolidation(
        report,
        expected_texts=["customer prefers email", "deploy needs approval"],
    )
    # One of two promoted facts matched gold -> precision 0.5; one of two gold
    # facts covered -> coverage 0.5. Matching is case/space-insensitive.
    assert ev.precision == 0.5
    assert ev.coverage == 0.5
    assert ev.gold_size == 2
    assert ev.facts_promoted == 2


def test_no_gold_yields_zero_precision_coverage() -> None:
    ev = evaluate_consolidation(_report("a", "b"))
    assert ev.precision == 0.0
    assert ev.coverage == 0.0
    assert ev.gold_size == 0


def test_dedup_ratio() -> None:
    report = _report("x", clusters=4)
    ev = evaluate_consolidation(report, total_episodes=10)
    assert ev.dedup_ratio == 0.6  # 1 - 4/10


def test_dedup_ratio_zero_without_total() -> None:
    ev = evaluate_consolidation(_report("x", clusters=4))
    assert ev.dedup_ratio == 0.0


def test_perfect_match() -> None:
    ev = evaluate_consolidation(_report("only fact"), expected_texts=["only fact"])
    assert ev.precision == 1.0
    assert ev.coverage == 1.0


def test_eval_report_round_trip() -> None:
    ev = evaluate_consolidation(_report("a"), expected_texts=["a"], total_episodes=2)
    restored = ConsolidationEvalReport.from_dict(ev.to_dict())
    assert restored == ev
    assert "precision" in ev.summary()

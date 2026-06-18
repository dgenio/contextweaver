"""Consolidation quality evaluation harness (issue #683).

:func:`evaluate_consolidation` scores a
:class:`~contextweaver.context.consolidation_types.ConsolidationReport` against
an optional gold set of expected fact texts and reports precision / coverage
plus deduplication metrics. It is pure-stdlib, offline, and deterministic: given
the same report and gold set it always produces the same numbers, so it can gate
quality regressions in CI fixtures.

Matching is on normalised text (lower-cased, whitespace-collapsed) so trivial
formatting differences between a promoted fact and its gold expectation do not
count as misses.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from contextweaver.context.consolidation_types import ConsolidationReport

__all__ = ["ConsolidationEvalReport", "evaluate_consolidation"]


def _normalise(text: str) -> str:
    """Lower-case and collapse whitespace for tolerant text matching."""
    return " ".join(text.lower().split())


@dataclass
class ConsolidationEvalReport:
    """Quality metrics for one consolidation run.

    Attributes:
        clusters_found: Number of clusters discovered.
        facts_promoted: Number of facts promoted.
        episodes_decayed: Number of episodes reported past the decay horizon.
        facts_decayed: Number of facts reported past the decay horizon.
        dedup_ratio: ``1 - clusters / total_episodes`` — fraction of episodic
            redundancy collapsed by clustering (``0.0`` when unknown).
        precision: Fraction of promoted facts present in the gold set
            (``0.0`` when no gold set is supplied).
        coverage: Fraction of gold facts that were promoted (``0.0`` when no
            gold set is supplied).
        gold_size: Number of distinct gold facts evaluated against.
    """

    clusters_found: int = 0
    facts_promoted: int = 0
    episodes_decayed: int = 0
    facts_decayed: int = 0
    dedup_ratio: float = 0.0
    precision: float = 0.0
    coverage: float = 0.0
    gold_size: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "clusters_found": self.clusters_found,
            "facts_promoted": self.facts_promoted,
            "episodes_decayed": self.episodes_decayed,
            "facts_decayed": self.facts_decayed,
            "dedup_ratio": self.dedup_ratio,
            "precision": self.precision,
            "coverage": self.coverage,
            "gold_size": self.gold_size,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConsolidationEvalReport:
        """Build a :class:`ConsolidationEvalReport` from a raw dict."""
        return cls(
            clusters_found=int(data.get("clusters_found", 0)),
            facts_promoted=int(data.get("facts_promoted", 0)),
            episodes_decayed=int(data.get("episodes_decayed", 0)),
            facts_decayed=int(data.get("facts_decayed", 0)),
            dedup_ratio=float(data.get("dedup_ratio", 0.0)),
            precision=float(data.get("precision", 0.0)),
            coverage=float(data.get("coverage", 0.0)),
            gold_size=int(data.get("gold_size", 0)),
        )

    def summary(self) -> str:
        """Return a compact, human-readable one-block summary."""
        return (
            f"Consolidation eval: clusters={self.clusters_found} "
            f"promoted={self.facts_promoted} dedup_ratio={self.dedup_ratio:.2f}\n"
            f"  precision={self.precision:.2f} coverage={self.coverage:.2f} "
            f"(gold={self.gold_size})\n"
            f"  decayed_episodes={self.episodes_decayed} decayed_facts={self.facts_decayed}"
        )


def evaluate_consolidation(
    report: ConsolidationReport,
    expected_texts: Iterable[str] | None = None,
    *,
    total_episodes: int | None = None,
) -> ConsolidationEvalReport:
    """Score *report* and return a :class:`ConsolidationEvalReport`.

    Args:
        report: The consolidation report to evaluate.
        expected_texts: Optional gold set of fact texts the run *should* have
            promoted. When supplied, precision and coverage are computed via
            normalised-text matching; otherwise both are ``0.0``.
        total_episodes: Total episodes the run saw, used for ``dedup_ratio``.
            When ``None`` or zero, ``dedup_ratio`` is ``0.0``.

    Returns:
        A populated :class:`ConsolidationEvalReport`.
    """
    promoted_norm = {_normalise(p.text) for p in report.promoted}
    gold_norm = {_normalise(t) for t in expected_texts} if expected_texts is not None else set()

    if gold_norm:
        hits = len(promoted_norm & gold_norm)
        precision = hits / len(promoted_norm) if promoted_norm else 0.0
        coverage = hits / len(gold_norm)
    else:
        precision = 0.0
        coverage = 0.0

    dedup_ratio = 0.0
    if total_episodes:
        dedup_ratio = round(1.0 - len(report.clusters) / total_episodes, 4)

    return ConsolidationEvalReport(
        clusters_found=len(report.clusters),
        facts_promoted=len(report.promoted),
        episodes_decayed=len(report.decayed_episode_ids),
        facts_decayed=len(report.decayed_fact_ids),
        dedup_ratio=dedup_ratio,
        precision=round(precision, 4),
        coverage=round(coverage, 4),
        gold_size=len(gold_norm),
    )

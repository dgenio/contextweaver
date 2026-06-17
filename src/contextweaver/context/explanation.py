"""Context-pack explanation traces for debug and evaluation (issue #291).

Surfaces *why* a single :meth:`~contextweaver.context.manager.ContextManager.build`
call produced the pack it did: which candidates were generated, what
scored where, which items the dependency-closure pulled in, which were
dropped by the sensitivity filter or budget, and which were collapsed
by deduplication.

Sister to :mod:`contextweaver.routing.explanation` (issue #226), which
does the same for routing decisions.  Both stay strictly pure-data:
no I/O, no randomness, no network calls.  Deterministic for a given
input (sorted keys, rounded floats).

Privacy: the explanation surfaces item *ids*, *kinds*, *sensitivity levels*,
*scores*, and *drop reasons* — never the raw ``ContextItem.text``, artifact
bytes, or ``args_schema`` content (which can carry sensitive payloads).  Same
posture as the routing explanation (see ``docs/agent-context/invariants.md``
"Do not put schemas on ChoiceCard").

Public API: :class:`ContextBuildExplanation` + :class:`CandidateExplanation`,
both versioned dataclasses with ``to_dict`` / ``from_dict``.  Opt-in via
``ContextManager.build(..., explain=True)``; the default ``explain=False``
keeps the public surface unchanged for existing callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

    from contextweaver.envelope import BuildStats
    from contextweaver.types import ContextItem, Phase

#: Schema version for :class:`ContextBuildExplanation`.  Bumped on
#: backwards-incompatible field changes (e.g. removing a key).
EXPLANATION_VERSION: int = 1


@dataclass
class CandidateExplanation:
    """Per-candidate explanation entry.

    Attributes:
        item_id: The candidate's :attr:`ContextItem.id`.
        kind: The candidate's :attr:`ContextItem.kind` (string form).
        sensitivity: The candidate's :attr:`ContextItem.sensitivity`
            (string form) — useful for understanding sensitivity-driven
            drops without re-fetching the item.
        score: The relevance score assigned by ``score_candidates``
            (``None`` for candidates dropped before scoring).
        included: ``True`` when the candidate landed in the final pack.
        drop_reason: Empty when ``included`` is ``True``. Otherwise the
            recorded exclusion reason. Common values include
            ``"sensitivity"``, ``"dedup"``, ``"kind_limit"``, and
            ``"budget"``; older payloads can also surface the
            legacy-only fallback ``"selection"``.
        dependency_closure: ``True`` when the candidate was pulled in
            by the dependency-closure stage rather than the phase
            filter — i.e. it scored lower than the cutoff but the
            pipeline added it because a higher-scoring item declared
            ``parent_id=<this item id>``.
    """

    item_id: str
    kind: str
    sensitivity: str
    score: float | None = None
    included: bool = False
    drop_reason: str = ""
    dependency_closure: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "item_id": self.item_id,
            "kind": self.kind,
            "sensitivity": self.sensitivity,
            "score": round(self.score, 4) if self.score is not None else None,
            "included": self.included,
            "drop_reason": self.drop_reason,
            "dependency_closure": self.dependency_closure,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CandidateExplanation:
        """Deserialise from a JSON-compatible dict."""
        return cls(
            item_id=str(data["item_id"]),
            kind=str(data["kind"]),
            sensitivity=str(data.get("sensitivity", "public")),
            score=float(data["score"]) if data.get("score") is not None else None,
            included=bool(data.get("included", False)),
            drop_reason=str(data.get("drop_reason", "")),
            dependency_closure=bool(data.get("dependency_closure", False)),
        )


@dataclass
class ContextBuildExplanation:
    """Structured rationale for a single context build (issue #291).

    Attributes:
        version: Schema version (currently :data:`EXPLANATION_VERSION`).
        phase: The :attr:`Phase` the build targeted.
        query: The query string passed to :meth:`ContextManager.build`.
        total_candidates: Number of candidates after dependency closure
            and before sensitivity filtering.
        included_count: Number of items in the final pack.
        dropped_count: Number of candidates not included.
        dropped_reasons: Aggregate counts per drop reason — mirrors the
            same dict on :class:`BuildStats` for ergonomic comparison.
        dependency_closures: How many items the dependency-closure
            stage added on top of the phase filter.
        sensitivity_drops: How many items the sensitivity filter
            removed (in ``drop`` mode).
        dedup_removed: How many items deduplication collapsed.
        budget_tokens: The effective phase budget after header/footer
            reservation.
        resolved_weights: The scoring weights applied for this build's phase
            (issue #487) — the per-phase override when registered, else the
            global config. Keys: the four ``ScoringConfig`` weight fields.
        candidates: Per-candidate :class:`CandidateExplanation` entries
            ordered by inclusion (included first, then dropped),
            then by descending score within each group (then by id,
            for determinism).
    """

    version: int = EXPLANATION_VERSION
    phase: str = "answer"
    query: str = ""
    total_candidates: int = 0
    included_count: int = 0
    dropped_count: int = 0
    dropped_reasons: dict[str, int] = field(default_factory=dict)
    dependency_closures: int = 0
    sensitivity_drops: int = 0
    dedup_removed: int = 0
    budget_tokens: int = 0
    resolved_weights: dict[str, float] = field(default_factory=dict)
    candidates: list[CandidateExplanation] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "version": self.version,
            "phase": self.phase,
            "query": self.query,
            "total_candidates": self.total_candidates,
            "included_count": self.included_count,
            "dropped_count": self.dropped_count,
            "dropped_reasons": dict(self.dropped_reasons),
            "dependency_closures": self.dependency_closures,
            "sensitivity_drops": self.sensitivity_drops,
            "dedup_removed": self.dedup_removed,
            "budget_tokens": self.budget_tokens,
            "resolved_weights": dict(self.resolved_weights),
            "candidates": [c.to_dict() for c in self.candidates],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextBuildExplanation:
        """Deserialise from a JSON-compatible dict.

        Missing keys fall back to dataclass defaults so older payloads
        round-trip cleanly when the schema is extended.
        """
        return cls(
            version=int(data.get("version", EXPLANATION_VERSION)),
            phase=str(data.get("phase", "answer")),
            query=str(data.get("query", "")),
            total_candidates=int(data.get("total_candidates", 0)),
            included_count=int(data.get("included_count", 0)),
            dropped_count=int(data.get("dropped_count", 0)),
            dropped_reasons=dict(data.get("dropped_reasons", {})),
            dependency_closures=int(data.get("dependency_closures", 0)),
            sensitivity_drops=int(data.get("sensitivity_drops", 0)),
            dedup_removed=int(data.get("dedup_removed", 0)),
            budget_tokens=int(data.get("budget_tokens", 0)),
            resolved_weights={
                str(k): float(v) for k, v in data.get("resolved_weights", {}).items()
            },
            candidates=[CandidateExplanation.from_dict(c) for c in data.get("candidates", [])],
        )


def build_explanation(
    *,
    phase: Phase,
    query: str,
    stats: BuildStats,
    sensitivity_dropped: Sequence[tuple[str, str, str]],
    sensitivity_drops: int,
    dedup_dropped: Sequence[tuple[str, str, str, float]],
    dedup_removed: int,
    closures: int,
    closure_added_ids: set[str],
    scored: Sequence[tuple[float, ContextItem]],
    selected_ids: set[str],
    budget_tokens: int,
    resolved_weights: dict[str, float],
) -> ContextBuildExplanation:
    """Assemble a :class:`ContextBuildExplanation` from pipeline state.

    Internal helper called by
    :meth:`~contextweaver.context.manager.ContextManager._build`.  Lives
    here to avoid putting explanation logic in ``manager.py``
    (issue #101) and to keep explanation rendering in a single
    self-contained module alongside the dataclasses.

    All inputs are passed positionally as keyword arguments to avoid
    accidental field-order regressions: the manager sets up these
    values inline and a future refactor must keep them aligned with
    each candidate's pipeline-stage history.

    Args:
        phase: The build's :attr:`Phase`.
        query: The build's user query.
        stats: The final :class:`BuildStats`.
        sensitivity_dropped: 3-tuples ``(id, kind, sensitivity_level)``
            for every item the sensitivity filter dropped.
        sensitivity_drops: Aggregate sensitivity-drop count.
        dedup_dropped: 4-tuples ``(id, kind, sensitivity_level,
            score)`` for every item the dedup stage collapsed —
            captured from the *pre-dedup* state so the explanation can
            still report the collapsed item's kind / sensitivity.
        dedup_removed: Aggregate dedup-collapse count.
        closures: Aggregate dependency-closure addition count.
        closure_added_ids: IDs added by dependency closure.
        scored: The post-dedup ``(score, item)`` list — used to
            populate ``CandidateExplanation.score``.
        selected_ids: IDs that made it into the final pack.
        budget_tokens: The effective phase budget after header /
            footer reservation.
        resolved_weights: The scoring weights applied for this phase (#487).

    Returns:
        A fully populated :class:`ContextBuildExplanation`.
    """
    candidates: list[CandidateExplanation] = []
    seen_ids: set[str] = set()

    # 1) Items the sensitivity filter dropped (never scored).
    for item_id, kind, sens in sensitivity_dropped:
        if item_id in seen_ids:
            continue
        candidates.append(
            CandidateExplanation(
                item_id=item_id,
                kind=kind,
                sensitivity=sens,
                score=None,
                included=False,
                drop_reason="sensitivity",
                dependency_closure=item_id in closure_added_ids,
            )
        )
        seen_ids.add(item_id)

    # 2) Items the dedup stage collapsed (had a score, then dropped).
    #    The caller hands us pre-dedup ``(id, kind, sens, score)``
    #    tuples so we can still report kind + sensitivity for items the
    #    surviving post-dedup view no longer remembers.
    for dropped_id, kind, sens, dedup_score in dedup_dropped:
        if dropped_id in seen_ids:
            continue
        candidates.append(
            CandidateExplanation(
                item_id=dropped_id,
                kind=kind,
                sensitivity=sens,
                score=dedup_score,
                included=False,
                drop_reason="dedup",
                dependency_closure=dropped_id in closure_added_ids,
            )
        )
        seen_ids.add(dropped_id)

    # Build the score lookup for the remaining survivors (post-dedup).
    score_by_id: dict[str, float] = {item.id: score for score, item in scored}
    drop_reason_by_id = {item.item_id: item.reason for item in stats.dropped_items}

    # 3) Every scored item — included or dropped by selection.
    for _score, item in scored:
        if item.id in seen_ids:
            continue
        included = item.id in selected_ids
        reason = ""
        if not included:
            reason = drop_reason_by_id.get(item.id, "selection")
        candidates.append(
            CandidateExplanation(
                item_id=item.id,
                kind=item.kind.value,
                sensitivity=item.sensitivity.value,
                score=score_by_id.get(item.id),
                included=included,
                drop_reason=reason,
                dependency_closure=item.id in closure_added_ids,
            )
        )
        seen_ids.add(item.id)

    # Sort: included first (by score desc), then dropped (by score desc,
    # then by id).  ``None`` scores sort last among their group.
    def _sort_key(c: CandidateExplanation) -> tuple[int, float, str]:
        bucket = 0 if c.included else 1
        # Negate score so highest-first; ``None`` → -inf (sorted last).
        s = -c.score if c.score is not None else float("inf")
        return (bucket, s, c.item_id)

    candidates.sort(key=_sort_key)

    return ContextBuildExplanation(
        version=EXPLANATION_VERSION,
        phase=phase.value,
        query=query,
        total_candidates=stats.total_candidates,
        included_count=stats.included_count,
        dropped_count=stats.dropped_count,
        dropped_reasons=dict(stats.dropped_reasons),
        dependency_closures=closures,
        sensitivity_drops=sensitivity_drops,
        dedup_removed=dedup_removed,
        budget_tokens=budget_tokens,
        resolved_weights=dict(resolved_weights),
        candidates=candidates,
    )


__all__ = [
    "EXPLANATION_VERSION",
    "CandidateExplanation",
    "ContextBuildExplanation",
]

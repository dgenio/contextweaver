"""History-aware re-routing helpers (issue #27, Phase 1).

Carries the execution-history signal the router needs to deprioritise
already-called tools and boost tools whose ``requires`` are satisfied by
``provides`` of previously-called tools.

The data class is deliberately small and JSON-friendly: it round-trips
through :meth:`to_dict` / :meth:`from_dict` and contains no references
to the routing graph or the event log itself.  The
:class:`~contextweaver.context.manager.ContextManager` builds one from
its event log inside :meth:`build_route_prompt`; downstream callers may
build one by hand.

Determinism guarantee: identical ``(query, history, items, graph)`` inputs
produce identical adjustments and identical routing output.  Adjustments
are computed in :func:`adjust_scores` and reported back to the caller via
:attr:`RouteResult.history_adjustments` so the decision surface stays
inspectable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from contextweaver.types import SelectableItem


#: Default penalty multiplier applied to ``score`` of an already-called tool.
#: Same magnitude as the one suggested in the issue body so the existing
#: integration test pattern (``r1.candidate_ids[0] not in r2.candidate_ids[:3]``)
#: holds out of the box.
DEFAULT_REPEAT_PENALTY: float = 0.5

#: Default weight applied to the ``last_result_summary`` similarity boost.
DEFAULT_RESULT_BOOST_WEIGHT: float = 0.3

#: Default boost added when a tool's ``requires`` are fully satisfied by
#: ``provides`` of already-called tools.
DEFAULT_DEPENDENCY_SATISFIED_BOOST: float = 0.2

#: Default penalty subtracted when a tool's ``depends_on`` references a
#: tool that has not yet been called.
DEFAULT_DEPENDENCY_UNSATISFIED_PENALTY: float = 0.2


@dataclass
class RouteHistory:
    """Execution-history signal for history-aware re-routing.

    Attributes:
        called_tool_ids: Tools already invoked in this session, in call
            order.  Order is **not** semantically meaningful — only
            membership matters for the repeat-penalty rule.
        last_result_summary: Summary of the most recent tool output
            (typically :attr:`~contextweaver.envelope.ResultEnvelope.summary`
            truncated).  ``None`` when no tool has been called yet.
        step_number: 1-based index of the agent step that is about to call
            :meth:`Router.route`.  Equal to ``len(called_tool_ids) + 1``
            when constructed automatically.
        repeat_penalty: Multiplier applied to scores of already-called
            tools (default :data:`DEFAULT_REPEAT_PENALTY`).
        result_boost_weight: Weight applied to the similarity between
            *last_result_summary* and each candidate (default
            :data:`DEFAULT_RESULT_BOOST_WEIGHT`).
    """

    called_tool_ids: list[str] = field(default_factory=list)
    last_result_summary: str | None = None
    step_number: int = 1
    repeat_penalty: float = DEFAULT_REPEAT_PENALTY
    result_boost_weight: float = DEFAULT_RESULT_BOOST_WEIGHT

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible dict representation."""
        return {
            "called_tool_ids": list(self.called_tool_ids),
            "last_result_summary": self.last_result_summary,
            "step_number": self.step_number,
            "repeat_penalty": self.repeat_penalty,
            "result_boost_weight": self.result_boost_weight,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RouteHistory:
        """Reconstruct from a previously-serialised dict.

        Missing keys fall back to dataclass defaults — so older payloads
        round-trip cleanly when the schema is extended in future versions.
        """
        return cls(
            called_tool_ids=list(data.get("called_tool_ids", [])),
            last_result_summary=data.get("last_result_summary"),
            step_number=int(data.get("step_number", 1)),
            repeat_penalty=float(data.get("repeat_penalty", DEFAULT_REPEAT_PENALTY)),
            result_boost_weight=float(data.get("result_boost_weight", DEFAULT_RESULT_BOOST_WEIGHT)),
        )


def _collect_provides(
    called_tool_ids: list[str],
    items: dict[str, SelectableItem],
) -> set[str]:
    """Return the union of ``provides`` capabilities of already-called tools.

    Tools without a ``provides`` field (or with ``provides=None``) contribute
    nothing.  Unknown ``called_tool_ids`` (not present in *items*) are
    silently skipped so the function tolerates a stale history.
    """
    out: set[str] = set()
    for tid in called_tool_ids:
        item = items.get(tid)
        if item is None or not item.provides:
            continue
        out.update(item.provides)
    return out


def adjust_scores(
    scored: list[tuple[str, float]],
    history: RouteHistory,
    items: dict[str, SelectableItem],
    *,
    result_similarity: dict[str, float] | None = None,
    dependency_satisfied_boost: float = DEFAULT_DEPENDENCY_SATISFIED_BOOST,
    dependency_unsatisfied_penalty: float = DEFAULT_DEPENDENCY_UNSATISFIED_PENALTY,
) -> tuple[list[tuple[str, float]], dict[str, float]]:
    """Apply *history* to *scored* and return ``(adjusted, deltas)``.

    The adjustment rules are deterministic and applied in this order so
    the deltas dict reports the net per-candidate change in one pass:

    1. **Repeat penalty** — multiply by :attr:`history.repeat_penalty` when
       the candidate id is in :attr:`history.called_tool_ids`.
    2. **Result-summary boost** — add ``weight * sim`` when
       *result_similarity* carries an entry for the candidate.
    3. **Dependency boost** — add *dependency_satisfied_boost* when the
       candidate's :attr:`SelectableItem.requires` are non-empty and are
       fully satisfied by ``provides`` of already-called tools.
    4. **Dependency penalty** — subtract *dependency_unsatisfied_penalty*
       when the candidate's :attr:`SelectableItem.depends_on` references
       a tool not present in :attr:`history.called_tool_ids`.

    Args:
        scored: Pre-adjustment ``(item_id, score)`` pairs.  Order
            preserved on the way in; output is re-sorted by ``(-score, id)``.
        history: The execution-history signal.
        items: Full pre-filter catalog (used to read ``depends_on`` /
            ``provides`` / ``requires`` metadata).
        result_similarity: Optional pre-computed similarity between
            ``history.last_result_summary`` and each candidate.  Skipped
            when ``None`` or when the candidate has no entry.
        dependency_satisfied_boost: Boost added when ``requires`` is
            satisfied by ``provides`` of already-called tools.
        dependency_unsatisfied_penalty: Penalty subtracted when
            ``depends_on`` references an uncalled tool.

    Returns:
        ``(adjusted_scored, deltas)`` — ``adjusted_scored`` is sorted by
        ``(-score, id)``; ``deltas`` maps ``item_id`` → net adjustment
        (only entries with a non-zero net change are included so
        downstream telemetry stays compact).
    """
    called = set(history.called_tool_ids)
    provides = _collect_provides(history.called_tool_ids, items)
    deltas: dict[str, float] = {}
    adjusted: list[tuple[str, float]] = []
    for item_id, score in scored:
        new_score = score
        # 1. repeat penalty
        if item_id in called:
            new_score = new_score * history.repeat_penalty
        # 2. result-summary boost
        if result_similarity is not None:
            sim = result_similarity.get(item_id)
            if sim is not None:
                new_score = new_score + history.result_boost_weight * sim
        # 3 + 4. dependency metadata
        item = items.get(item_id)
        if item is not None:
            if item.requires and all(cap in provides for cap in item.requires):
                new_score = new_score + dependency_satisfied_boost
            if item.depends_on and any(dep not in called for dep in item.depends_on):
                new_score = new_score - dependency_unsatisfied_penalty
        delta = new_score - score
        if delta != 0.0:
            deltas[item_id] = delta
        adjusted.append((item_id, new_score))
    adjusted.sort(key=lambda x: (-x[1], x[0]))
    return adjusted, deltas

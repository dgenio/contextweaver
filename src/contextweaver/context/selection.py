"""Budget-aware selection for the contextweaver Context Engine.

Selects items from the scored, deduplicated candidate list up to the
configured token budget for the current phase.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from contextweaver.config import ContextBudget, ContextPolicy
from contextweaver.protocols import TokenEstimator
from contextweaver.types import ContextItem, Phase

logger = logging.getLogger("contextweaver.context")


@dataclass
class _SelectionOutcome:
    """Raw selection-stage outcome consumed by the build pipeline."""

    selected: list[ContextItem] = field(default_factory=list)
    dropped: list[tuple[ContextItem, str]] = field(default_factory=list)
    tokens_per_section: dict[str, int] = field(default_factory=dict)
    budget_overruns: list[tuple[int, int]] = field(default_factory=list)


def select_and_pack(
    scored: list[tuple[float, ContextItem]],
    phase: Phase,
    budget: ContextBudget,
    policy: ContextPolicy,
    estimator: TokenEstimator,
) -> _SelectionOutcome:
    """Select items up to the phase budget, enforcing per-kind limits.

    Iterates through *scored* in descending order and greedily includes items
    until the token budget is exhausted or all candidates are considered.

    Args:
        scored: ``(score, item)`` tuples in descending score order.
        phase: The active execution phase.
        budget: The token budget configuration.
        policy: The context policy (used for per-kind limits).
        estimator: Token estimator for items whose ``token_estimate`` is zero.

    Returns:
        The raw selection outcome. The enclosing build pipeline owns final
        :class:`~contextweaver.envelope.BuildStats` construction.
    """
    token_limit = budget.for_phase(phase)
    max_per_kind = policy.max_items_per_kind

    selected: list[ContextItem] = []
    tokens_used = 0
    kind_counts: dict[str, int] = {}
    dropped: list[tuple[ContextItem, str]] = []
    budget_overruns: list[tuple[int, int]] = []

    for _, item in scored:
        kind_key = item.kind.value

        # Per-kind limit
        kind_limit = max_per_kind.get(item.kind, 50)
        if kind_counts.get(kind_key, 0) >= kind_limit:
            dropped.append((item, "kind_limit"))
            continue

        # Token estimate
        token_count = item.token_estimate or estimator.estimate(item.text)

        # Budget check
        if tokens_used + token_count > token_limit:
            dropped.append((item, "budget"))
            budget_overruns.append((tokens_used + token_count, token_limit))
            continue

        selected.append(item)
        tokens_used += token_count
        kind_counts[kind_key] = kind_counts.get(kind_key, 0) + 1

    # Build stats
    tokens_per_section: dict[str, int] = {}
    for item in selected:
        k = item.kind.value
        t = item.token_estimate or estimator.estimate(item.text)
        tokens_per_section[k] = tokens_per_section.get(k, 0) + t

    dropped_reasons: dict[str, int] = {}
    for _item, reason in dropped:
        dropped_reasons[reason] = dropped_reasons.get(reason, 0) + 1
    logger.debug(
        "select_and_pack: included=%d, dropped=%d, tokens=%d/%d, reasons=%s",
        len(selected),
        len(dropped),
        tokens_used,
        token_limit,
        dropped_reasons,
    )
    return _SelectionOutcome(
        selected=selected,
        dropped=dropped,
        tokens_per_section=tokens_per_section,
        budget_overruns=budget_overruns,
    )

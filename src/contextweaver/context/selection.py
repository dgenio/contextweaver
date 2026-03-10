"""Budget-aware selection for the contextweaver Context Engine.

Selects items from the scored, deduplicated candidate list up to the
configured token budget for the current phase.
"""

from __future__ import annotations

import logging

from contextweaver.config import ContextBudget, ContextPolicy
from contextweaver.envelope import BuildStats
from contextweaver.protocols import TokenEstimator
from contextweaver.types import ContextItem, Phase

logger = logging.getLogger("contextweaver.context")


def select_and_pack(
    scored: list[tuple[float, ContextItem]],
    phase: Phase,
    budget: ContextBudget,
    policy: ContextPolicy,
    estimator: TokenEstimator,
) -> tuple[list[ContextItem], BuildStats]:
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
        A 2-tuple ``(selected_items, stats)``.
    """
    token_limit = budget.for_phase(phase)
    max_per_kind = policy.max_items_per_kind

    selected: list[ContextItem] = []
    tokens_used = 0
    kind_counts: dict[str, int] = {}
    dropped_reasons: dict[str, int] = {}

    for _, item in scored:
        kind_key = item.kind.value

        # Per-kind limit
        kind_limit = max_per_kind.get(item.kind, 50)
        if kind_counts.get(kind_key, 0) >= kind_limit:
            dropped_reasons["kind_limit"] = dropped_reasons.get("kind_limit", 0) + 1
            continue

        # Token estimate
        token_count = item.token_estimate or estimator.estimate(item.text)

        # Budget check
        if tokens_used + token_count > token_limit:
            dropped_reasons["budget"] = dropped_reasons.get("budget", 0) + 1
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

    total_candidates = len(scored)
    included = len(selected)
    dropped = total_candidates - included

    stats = BuildStats(
        tokens_per_section=tokens_per_section,
        total_candidates=total_candidates,
        included_count=included,
        dropped_count=dropped,
        dropped_reasons=dropped_reasons,
    )
    logger.debug(
        "select_and_pack: included=%d, dropped=%d, tokens=%d/%d, reasons=%s",
        included,
        dropped,
        tokens_used,
        token_limit,
        dropped_reasons,
    )
    return selected, stats

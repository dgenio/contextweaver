"""Candidate scoring for the contextweaver Context Engine (Stage 2).

Scores each ContextItem based on recency, tag overlap, kind priority,
and token cost penalty.
"""

from __future__ import annotations

from contextweaver._utils import jaccard
from contextweaver.config import ScoringConfig
from contextweaver.types import ContextItem, ItemKind, Phase

# Static kind priority per phase
_KIND_PRIORITY: dict[Phase, dict[ItemKind, float]] = {
    Phase.ROUTE: {
        ItemKind.TOOL_CALL: 0.9,
        ItemKind.POLICY: 0.8,
        ItemKind.USER_TURN: 0.7,
        ItemKind.PLAN_STATE: 0.6,
        ItemKind.AGENT_MSG: 0.4,
        ItemKind.DOC_SNIPPET: 0.3,
        ItemKind.TOOL_RESULT: 0.2,
        ItemKind.MEMORY_FACT: 0.5,
    },
    Phase.CALL: {
        ItemKind.TOOL_CALL: 0.9,
        ItemKind.USER_TURN: 0.8,
        ItemKind.POLICY: 0.7,
        ItemKind.PLAN_STATE: 0.6,
        ItemKind.AGENT_MSG: 0.5,
        ItemKind.DOC_SNIPPET: 0.4,
        ItemKind.TOOL_RESULT: 0.3,
        ItemKind.MEMORY_FACT: 0.5,
    },
    Phase.INTERPRET: {
        ItemKind.TOOL_RESULT: 0.9,
        ItemKind.TOOL_CALL: 0.8,
        ItemKind.USER_TURN: 0.7,
        ItemKind.AGENT_MSG: 0.6,
        ItemKind.DOC_SNIPPET: 0.5,
        ItemKind.POLICY: 0.4,
        ItemKind.PLAN_STATE: 0.3,
        ItemKind.MEMORY_FACT: 0.5,
    },
    Phase.ANSWER: {
        ItemKind.USER_TURN: 0.9,
        ItemKind.TOOL_RESULT: 0.8,
        ItemKind.AGENT_MSG: 0.7,
        ItemKind.TOOL_CALL: 0.6,
        ItemKind.DOC_SNIPPET: 0.5,
        ItemKind.MEMORY_FACT: 0.5,
        ItemKind.PLAN_STATE: 0.3,
        ItemKind.POLICY: 0.4,
    },
}


def score_candidates(
    candidates: list[ContextItem],
    phase: Phase,
    goal_tokens: set[str],
    hint_tags: set[str],
    budget_tokens: int,
    config: ScoringConfig,
) -> list[tuple[ContextItem, float]]:
    """Score each candidate.

    Returns (item, score) sorted by score desc. Ties broken by item.id.
    """
    if not candidates:
        return []

    n = len(candidates)
    phase_priorities = _KIND_PRIORITY.get(phase, {})

    scored: list[tuple[ContextItem, float]] = []

    for i, item in enumerate(candidates):
        # Recency: linear decay 1.0 (newest) to 0.0 (oldest)
        recency_norm = (i + 1) / n if n > 0 else 0.0

        # Tag match: jaccard between item tags and (hint_tags union goal_tokens)
        item_tags = set(item.metadata.get("tags", []))
        combined_tags = hint_tags | goal_tokens
        tag_match_norm = jaccard(item_tags, combined_tags) if combined_tags else 0.0

        # Kind priority
        kind_priority_norm = phase_priorities.get(item.kind, 0.3)

        # Token cost penalty
        token_cost_norm = item.token_estimate / budget_tokens if budget_tokens > 0 else 0.0

        score = (
            config.recency_weight * recency_norm
            + config.tag_match_weight * tag_match_norm
            + config.kind_priority_weight * kind_priority_norm
            - config.token_cost_penalty * token_cost_norm
        )
        scored.append((item, score))

    scored.sort(key=lambda x: (-x[1], x[0].id))
    return scored

"""Candidate scoring for the contextweaver Context Engine.

Scores each :class:`~contextweaver.types.ContextItem` based on recency,
tag overlap with the query, item-kind priority, and token cost penalty.
"""

from __future__ import annotations

from contextweaver._utils import jaccard, tokenize
from contextweaver.config import ScoringConfig
from contextweaver.types import ContextItem, ItemKind

# Higher value → higher priority when included in context
_KIND_PRIORITY: dict[ItemKind, float] = {
    ItemKind.policy: 1.0,
    ItemKind.plan_state: 0.9,
    ItemKind.user_turn: 0.85,
    ItemKind.agent_msg: 0.7,
    ItemKind.tool_call: 0.6,
    ItemKind.tool_result: 0.55,
    ItemKind.memory_fact: 0.5,
    ItemKind.doc_snippet: 0.4,
}


def score_item(
    item: ContextItem,
    query: str,
    position: int,
    total: int,
    query_tags: list[str],
    config: ScoringConfig,
) -> float:
    """Compute a relevance score for *item*.

    Args:
        item: The candidate to score.
        query: The user query string.
        position: Zero-based insertion index of *item* in the event log.
        total: Total number of items in the event log.
        query_tags: Tags extracted from the query / metadata context.
        config: Scoring weight configuration.

    Returns:
        A non-negative float; higher means more relevant / higher priority.
    """
    # Recency: items near the end of the log score higher
    recency = (position + 1) / max(total, 1)

    # Tag overlap
    item_tokens = tokenize(item.text)
    query_tokens = tokenize(query)
    text_sim = jaccard(item_tokens, query_tokens)

    item_tag_set = set(item.tags) if hasattr(item, "tags") else set()
    query_tag_set = set(query_tags)
    tag_sim = jaccard(item_tag_set, query_tag_set) if query_tag_set else text_sim

    # Kind priority
    kind_score = _KIND_PRIORITY.get(item.kind, 0.3)

    # Token cost penalty: penalise very large items
    token_penalty = 1.0 / (1.0 + item.token_estimate / 500.0)

    score = (
        config.recency_weight * recency
        + config.tag_match_weight * tag_sim
        + config.kind_priority_weight * kind_score
        + config.token_cost_penalty * token_penalty
    )
    return score


def score_candidates(
    items: list[ContextItem],
    query: str,
    query_tags: list[str],
    config: ScoringConfig,
) -> list[tuple[float, ContextItem]]:
    """Score all *items* and return them sorted by descending score.

    Ties are broken by item ``id`` (lexicographic) for determinism.

    Args:
        items: Candidate items to score.
        query: The user query string.
        query_tags: Tags extracted from the query context.
        config: Scoring configuration.

    Returns:
        A list of ``(score, item)`` tuples, highest score first.
    """
    total = len(items)
    scored = [
        (score_item(item, query, i, total, query_tags, config), item)
        for i, item in enumerate(items)
    ]
    scored.sort(key=lambda x: (-x[0], x[1].id))
    return scored

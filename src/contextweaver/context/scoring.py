"""Candidate scoring for the contextweaver Context Engine.

Scores each :class:`~contextweaver.types.ContextItem` based on recency,
tag overlap with the query, item-kind priority, and token cost penalty.
"""

from __future__ import annotations

import logging

from contextweaver._utils import jaccard, tokenize
from contextweaver.config import ScoringConfig
from contextweaver.types import ContextItem, ItemKind

logger = logging.getLogger("contextweaver.context")

# Higher value → higher priority when included in context.  Overridable
# per-build via ``ScoringConfig.kind_priority`` (issue #487); this table is the
# default when no override is supplied.
_KIND_PRIORITY: dict[ItemKind, float] = {
    ItemKind.policy: 1.0,
    ItemKind.plan_state: 0.9,
    ItemKind.user_turn: 0.85,
    ItemKind.agent_msg: 0.7,
    ItemKind.tool_call: 0.6,
    ItemKind.tool_result: 0.55,
    ItemKind.memory_fact: 0.5,
    # Retrieved/RAG payloads sit just above authored doc snippets: they are
    # pulled in for the active query, so they are marginally more relevant than
    # static documents by default (issue #411).
    ItemKind.retrieved_doc: 0.45,
    ItemKind.doc_snippet: 0.4,
}


def _resolve_kind_priority(config: ScoringConfig, kind: ItemKind) -> float:
    """Return the priority for *kind*, honoring a ``kind_priority`` override.

    Resolution order (issue #487): ``config.kind_priority`` → the built-in
    :data:`_KIND_PRIORITY` table → ``0.3`` for any unlisted kind.
    """
    if config.kind_priority is not None and kind in config.kind_priority:
        return config.kind_priority[kind]
    return _KIND_PRIORITY.get(kind, 0.3)


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

    item_tag_set: set[str] = set(item.metadata.get("tags", []))
    query_tag_set = set(query_tags)
    tag_sim = jaccard(item_tag_set, query_tag_set) if query_tag_set else text_sim

    # Kind priority (built-in table, overridable via config.kind_priority)
    kind_score = _resolve_kind_priority(config, item.kind)

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
    if scored:
        logger.debug(
            "score_candidates: total=%d, top_score=%.4f, bottom_score=%.4f",
            len(scored),
            scored[0][0],
            scored[-1][0],
        )
    return scored

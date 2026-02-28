"""Candidate deduplication for the contextweaver Context Engine.

Removes near-duplicate items from the candidate list before the selection
pass.  Uses Jaccard similarity on tokenised text to identify duplicates.
"""

from __future__ import annotations

from contextweaver._utils import jaccard, tokenize
from contextweaver.types import ContextItem


def deduplicate_candidates(
    scored: list[tuple[float, ContextItem]],
    similarity_threshold: float = 0.85,
) -> tuple[list[tuple[float, ContextItem]], int]:
    """Remove near-duplicate items from a scored candidate list.

    When two items have a Jaccard similarity ≥ *similarity_threshold* on
    their tokenised text, the one with the lower score (or later ``id`` if
    tied) is dropped.

    Args:
        scored: A list of ``(score, item)`` tuples in *descending* score order
            (as returned by :func:`~contextweaver.context.scoring.score_candidates`).
        similarity_threshold: Jaccard similarity above which two items are
            considered duplicates.  Default: 0.85.

    Returns:
        A 2-tuple ``(deduplicated, removed_count)`` where *deduplicated* is
        the filtered list in the same order and *removed_count* is how many
        items were dropped.
    """
    kept: list[tuple[float, ContextItem]] = []
    kept_tokens: list[set[str]] = []
    removed = 0

    for score, item in scored:
        tokens = tokenize(item.text)
        duplicate = False
        for existing_tokens in kept_tokens:
            if jaccard(tokens, existing_tokens) >= similarity_threshold:
                duplicate = True
                break
        if not duplicate:
            kept.append((score, item))
            kept_tokens.append(tokens)
        else:
            removed += 1

    return kept, removed

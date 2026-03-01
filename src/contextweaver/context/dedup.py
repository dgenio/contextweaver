"""Candidate deduplication for the contextweaver Context Engine (Stage 3).

Removes duplicate items by content hash.
"""

from __future__ import annotations

import hashlib

from contextweaver.types import ContextItem


def deduplicate_candidates(
    scored: list[tuple[ContextItem, float]],
) -> tuple[list[tuple[ContextItem, float]], int]:
    """Remove duplicate items by content hash (hash of item.text).

    If two items have identical text, keep the higher-scored one.
    Returns (deduplicated list, count of items removed).

    # FUTURE: merge compression -- merge adjacent same-kind items sharing parent_id.
    """
    seen_hashes: dict[str, int] = {}
    kept: list[tuple[ContextItem, float]] = []
    removed = 0

    for item, score in scored:
        text_hash = hashlib.md5(item.text.encode("utf-8")).hexdigest()
        if text_hash in seen_hashes:
            removed += 1
        else:
            seen_hashes[text_hash] = len(kept)
            kept.append((item, score))

    return kept, removed

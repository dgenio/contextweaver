"""Pre-scoring filters and uncertainty helpers for the routing engine.

These helpers are the catalog-side and result-side hooks consumed by
:class:`~contextweaver.routing.router.Router`:

* :func:`augment_query` — append conversation hints to the scoring
  query (issue #116).
* :func:`filter_items` — apply negative routing exclusions
  (issue #112) and toolset gating (issue #22) before beam search.
* :func:`suggest_clarifying_question` — propose a disambiguation
  prompt when the top candidates are too close to call (issue #14).

Pulling them out of ``router.py`` keeps that module focused on the
beam-search algorithm and within the ≤ 300 line per-module guideline.
"""

from __future__ import annotations

from contextweaver.types import SelectableItem


def augment_query(query: str, hints: list[str] | None) -> str:
    """Return *query* with whitespace-joined *hints* appended.

    Hints are appended after the query so the original token order
    drives ranking ties.  Empty / whitespace-only hints are dropped.

    Args:
        query: User query string.
        hints: Optional context hints from the caller.

    Returns:
        Either *query* unchanged or ``"<query> <hint1> <hint2>..."``.
    """
    if not hints:
        return query
    cleaned = [h.strip() for h in hints if h and h.strip()]
    if not cleaned:
        return query
    return f"{query} {' '.join(cleaned)}"


def filter_items(
    items: dict[str, SelectableItem],
    *,
    exclude_ids: set[str] | None,
    exclude_tags: set[str] | None,
    allowed_namespaces: set[str] | None,
    allowed_tags: set[str] | None,
) -> tuple[dict[str, SelectableItem], int, int]:
    """Apply gating + exclusion filters to *items*.

    Negative routing (issue #112) is a blacklist: an item is dropped
    when its id is in *exclude_ids* or any of its tags is in
    *exclude_tags*.

    Toolset gating (issue #22) is a whitelist: an item is dropped when
    its namespace is not in *allowed_namespaces* (when provided) or
    when none of its tags appears in *allowed_tags* (when provided).

    Args:
        items: Catalog items keyed by id.
        exclude_ids: IDs to drop (issue #112).
        exclude_tags: Tags whose presence drops an item (issue #112).
        allowed_namespaces: Whitelist of allowed namespaces (issue #22).
            ``None`` disables the namespace gate.
        allowed_tags: Whitelist of required tags (issue #22) — at least
            one tag must overlap.  ``None`` disables the tag gate.

    Returns:
        ``(filtered_items, excluded_count, gated_count)``.
    """
    filtered: dict[str, SelectableItem] = {}
    excluded = 0
    gated = 0
    for item_id, item in items.items():
        if exclude_ids and item_id in exclude_ids:
            excluded += 1
            continue
        if exclude_tags and exclude_tags.intersection(item.tags):
            excluded += 1
            continue
        if allowed_namespaces is not None and item.namespace not in allowed_namespaces:
            gated += 1
            continue
        if allowed_tags is not None and not allowed_tags.intersection(item.tags):
            gated += 1
            continue
        filtered[item_id] = item
    return filtered, excluded, gated


def suggest_clarifying_question(
    query: str,
    top_items: list[SelectableItem],
) -> str | None:
    """Build a clarifying-question string for ambiguous routes (issue #14).

    The question is rendered from the most distinguishing dimension of
    the top candidates:

    1. Distinct namespaces, when at least two are present.
    2. Distinct names, otherwise.

    Args:
        query: Original user query.
        top_items: Top candidates from the routing run (use the rank-1
            and rank-2 candidates at minimum).

    Returns:
        A short surface-level prompt, or ``None`` when no useful
        discriminator can be inferred.
    """
    if len(top_items) < 2:
        return None
    namespaces = sorted({it.namespace for it in top_items if it.namespace})
    if len(namespaces) >= 2:
        joined = ", ".join(repr(ns) for ns in namespaces[:3])
        return f"Did you mean {joined}? Multiple namespaces matched {query!r}."
    names = [it.name for it in top_items[:3]]
    joined = ", ".join(repr(n) for n in names)
    return f"Multiple tools could handle {query!r}: {joined}. Which did you mean?"


__all__ = [
    "augment_query",
    "filter_items",
    "suggest_clarifying_question",
]

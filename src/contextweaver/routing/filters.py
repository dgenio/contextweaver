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

import logging

from contextweaver.types import SelectableItem

logger = logging.getLogger("contextweaver.routing")

#: A ranked candidate as produced by the navigator/rerank stages:
#: ``(item_id, (score, beam_path))``.
RankedEntry = tuple[str, tuple[float, list[str]]]


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
    augmented = f"{query} {' '.join(cleaned)}"
    logger.debug(
        "augment_query: hints=%d, original=%r, augmented=%r",
        len(cleaned),
        query,
        augmented,
    )
    return augmented


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


def compose_shortlist(
    ranked: list[RankedEntry],
    items: dict[str, SelectableItem],
    *,
    top_k: int,
    pin_ids: set[str] | None = None,
    namespace_quota: int | None = None,
) -> list[RankedEntry]:
    """Compose the final shortlist with pinning and diversity quotas (issue #509).

    Operates on the fully ranked ``(item_id, (score, path))`` list *after* all
    scoring stages, replacing a plain ``ranked[:top_k]`` slice.  When neither
    *pin_ids* nor *namespace_quota* is supplied the result is byte-identical to
    that slice, so unconfigured routing is unchanged.

    Composition rules:

    * **Pinned items** (those in *pin_ids* that survived filtering and exist in
      *items*) always appear and occupy the first slots, bypassing the
      namespace quota.  A pinned item that the navigator scored keeps that
      score and ranked position; a pinned item the navigator never reached is
      still force-included with score ``0.0`` and an empty path, in sorted-id
      order, so pinning holds *regardless of query relevance*.  Pinning may push
      the shortlist beyond *top_k* when more than *top_k* items are pinned — an
      explicit operator choice.
    * **Diversity quota** caps how many *non-pinned* items a single namespace
      may contribute, so one large upstream cannot monopolise the remaining
      slots.  Items beyond a namespace's quota are skipped, not reordered.

    Args:
        ranked: Candidates sorted by descending score, ties broken by id.
        items: The active (post-filter) catalog items keyed by id.
        top_k: Target shortlist size for the non-pinned fill.
        pin_ids: Item IDs to always include.  ``None`` / empty disables pinning.
        namespace_quota: Max non-pinned items per namespace (must be ≥ 1).
            ``None`` disables the quota.

    Returns:
        The composed shortlist, preserving the ranked ordering within the
        pinned block and within the fill block.
    """
    if not pin_ids and namespace_quota is None:
        return ranked[: max(0, top_k)]

    pins = pin_ids or set()
    selected: list[RankedEntry] = []
    selected_ids: set[str] = set()

    # Pinned items the navigator scored: keep their score and ranked order.
    for entry in ranked:
        item_id = entry[0]
        if item_id in pins and item_id in items and item_id not in selected_ids:
            selected.append(entry)
            selected_ids.add(item_id)

    # Pinned items the navigator never reached: force-include them so pinning
    # holds regardless of query relevance.  Sorted id order keeps it deterministic.
    for item_id in sorted(pins):
        if item_id in items and item_id not in selected_ids:
            selected.append((item_id, (0.0, [])))
            selected_ids.add(item_id)

    namespace_counts: dict[str, int] = {}
    for entry in ranked:
        if len(selected) >= top_k:
            break
        item_id = entry[0]
        if item_id in selected_ids:
            continue
        namespace = items[item_id].namespace if item_id in items else ""
        if namespace_quota is not None and namespace_counts.get(namespace, 0) >= namespace_quota:
            continue
        selected.append(entry)
        selected_ids.add(item_id)
        namespace_counts[namespace] = namespace_counts.get(namespace, 0) + 1

    return selected


__all__ = [
    "RankedEntry",
    "augment_query",
    "compose_shortlist",
    "filter_items",
    "suggest_clarifying_question",
]

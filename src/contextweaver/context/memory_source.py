"""Memory-source adapter helpers for phase-aware context compilation.

Memory entries materialise into :class:`~contextweaver.types.ContextItem`
objects of kind :attr:`~contextweaver.types.ItemKind.memory_fact`, then flow
through the existing Context Engine pipeline unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from contextweaver.context.memory_fixture import JsonFixtureMemorySource
from contextweaver.context.memory_types import PHASE_SCOPE_PREFERENCES, MemoryEntry
from contextweaver.protocols import MemorySource, TokenEstimator
from contextweaver.tokens import heuristic_counter
from contextweaver.types import ContextItem, ItemKind, Phase

logger = logging.getLogger("contextweaver.context")


def _estimate_cost(text: str, estimator: TokenEstimator | None) -> int:
    """Return a positive token estimate for *text*.

    Falls back to the canonical script-aware heuristic counter (issue #530)
    rather than an inline ``len // 4`` literal when no estimator is supplied,
    so every budget number flows through one source of truth.
    """
    counter = estimator if estimator is not None else heuristic_counter()
    return max(1, int(counter.estimate(text)))


def _contextweaver_namespace(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return a mutable reserved namespace, ignoring malformed user values."""
    raw_namespace = metadata.get("_contextweaver", {})
    if not isinstance(raw_namespace, Mapping):
        return {}
    return dict(raw_namespace)


def memory_entries_to_context_items(
    entries: list[MemoryEntry],
    *,
    estimator: TokenEstimator | None = None,
    now: float | None = None,
) -> list[ContextItem]:
    """Materialise *entries* into :class:`ContextItem` candidates.

    Expired entries are filtered out. Each surviving entry becomes a
    :class:`ContextItem` of kind
    :attr:`~contextweaver.types.ItemKind.memory_fact`; the entry's sensitivity
    level is preserved and enforced downstream by the existing sensitivity
    stage.

    Args:
        entries: Source entries.
        estimator: Optional token estimator. When omitted, the canonical
            script-aware heuristic counter is used with a minimum cost of one
            token per entry.
        now: UNIX seconds reference time for expiry filtering.

    Returns:
        A list of context items in the same order as *entries*.
    """
    result: list[ContextItem] = []
    for entry in entries:
        if entry.is_expired(now=now):
            continue
        merged_metadata: dict[str, Any] = dict(entry.metadata)
        if entry.tags:
            merged_metadata.setdefault("tags", list(entry.tags))
        cw_ns = _contextweaver_namespace(merged_metadata)
        cw_ns["memory_source"] = {
            "id": entry.id,
            "source": entry.source,
            "scope": entry.scope,
            "confidence": entry.confidence,
            "timestamp": entry.timestamp,
        }
        merged_metadata["_contextweaver"] = cw_ns
        result.append(
            ContextItem(
                id=f"memory:{entry.id}",
                kind=ItemKind.memory_fact,
                text=entry.text,
                token_estimate=_estimate_cost(entry.text, estimator),
                sensitivity=entry.sensitivity,
                metadata=merged_metadata,
            )
        )
    return result


def select_memory_for_phase(
    source: MemorySource,
    query: str,
    phase: Phase,
    *,
    budget_tokens: int,
    estimator: TokenEstimator | None = None,
    now: float | None = None,
    max_entries: int | None = None,
) -> list[ContextItem]:
    """Pull entries from *source*, convert to items, enforce a token budget.

    The function calls :meth:`MemorySource.select`, materialises via
    :func:`memory_entries_to_context_items`, and greedily packs entries whose
    positive token cost fits the remaining budget.

    Args:
        source: Any :class:`~contextweaver.protocols.MemorySource`.
        query: Selection query.
        phase: Active execution phase.
        budget_tokens: Hard cap on cumulative token estimate.
        estimator: Optional token estimator.
        now: UNIX seconds reference time for expiry filtering.
        max_entries: Optional hard cap forwarded to ``source.select``.

    Returns:
        Context items that fit within *budget_tokens* in source relevance order.
    """
    if budget_tokens <= 0:
        return []
    entries = source.select(query, phase, now=now, max_entries=max_entries)
    items = memory_entries_to_context_items(entries, estimator=estimator, now=now)
    packed: list[ContextItem] = []
    remaining = budget_tokens
    for item in items:
        cost = max(1, int(item.token_estimate))
        if cost > remaining:
            continue
        packed.append(item)
        remaining -= cost
    logger.debug(
        "select_memory_for_phase: phase=%s, packed=%d/%d, remaining_budget=%d/%d",
        phase.value,
        len(packed),
        len(items),
        remaining,
        budget_tokens,
    )
    return packed


__all__ = [
    "PHASE_SCOPE_PREFERENCES",
    "JsonFixtureMemorySource",
    "MemoryEntry",
    "memory_entries_to_context_items",
    "select_memory_for_phase",
]

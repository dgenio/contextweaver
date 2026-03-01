"""Budget-aware selection for the contextweaver Context Engine (Stage 4).

Greedy budget packing with dependency closure.
"""

from __future__ import annotations

from contextweaver.exceptions import ItemNotFoundError
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.types import ContextItem


def select_and_pack(
    scored: list[tuple[ContextItem, float]],
    budget_tokens: int,
    event_log: InMemoryEventLog,
) -> tuple[list[ContextItem], list[tuple[str, str]], int]:
    """Greedy budget packing with dependency closure.

    Returns: (included_items, excluded_with_reasons, dependency_closure_count)
    Reasons: "budget", "dependency_closure_budget", "lower_score"
    """
    included: list[ContextItem] = []
    included_ids: set[str] = set()
    excluded: list[tuple[str, str]] = []
    tokens_used = 0
    closures = 0

    for item, score in scored:
        if item.id in included_ids:
            continue

        tokens_needed = item.token_estimate

        # Dependency closure: if TOOL_RESULT with parent_id
        parent_item: ContextItem | None = None
        parent_tokens = 0
        if item.kind.value == "tool_result" and item.parent_id:
            if item.parent_id not in included_ids:
                try:
                    parent_item = event_log.get_sync(item.parent_id)
                    parent_tokens = parent_item.token_estimate
                except ItemNotFoundError:
                    parent_item = None

        total_needed = tokens_needed + parent_tokens

        if tokens_used + total_needed > budget_tokens:
            if parent_item:
                excluded.append((item.id, "dependency_closure_budget"))
            else:
                excluded.append((item.id, "budget"))
            continue

        # Include parent first if needed
        if parent_item and parent_item.id not in included_ids:
            included.append(parent_item)
            included_ids.add(parent_item.id)
            tokens_used += parent_tokens
            closures += 1

        included.append(item)
        included_ids.add(item.id)
        tokens_used += tokens_needed

    # Mark remaining scored items as lower_score
    for item, _ in scored:
        if item.id not in included_ids and not any(eid == item.id for eid, _ in excluded):
            excluded.append((item.id, "lower_score"))

    return included, excluded, closures

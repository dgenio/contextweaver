"""Candidate generation for the contextweaver Context Engine.

The :func:`generate_candidates` function is the first step of the context
pipeline.  It reads all items from the event log and applies basic phase
filtering to produce an initial candidate list.
"""

from __future__ import annotations

from contextweaver.config import ContextPolicy
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.types import ContextItem, Phase


def generate_candidates(
    event_log: InMemoryEventLog,
    phase: Phase,
    policy: ContextPolicy,
) -> list[ContextItem]:
    """Return a filtered list of candidate items for context compilation.

    Only items whose ``kind`` is permitted by *policy* for the current *phase*
    are returned.

    Args:
        event_log: The event log to read from.
        phase: The active execution phase.
        policy: The context policy governing which item kinds are allowed.

    Returns:
        A list of :class:`~contextweaver.types.ContextItem` in log order.
    """
    allowed = set(policy.allowed_kinds_per_phase.get(phase, []))
    return [item for item in event_log.all() if item.kind in allowed]


def resolve_dependency_closure(
    items: list[ContextItem],
    event_log: InMemoryEventLog,
) -> tuple[list[ContextItem], int]:
    """Expand *items* by pulling in parent items referenced via ``parent_id``.

    Walks the parent chain for each item and adds any missing ancestors that
    are not already in the candidate list.

    Args:
        items: Initial candidate list.
        event_log: Source event log for ancestor look-up.

    Returns:
        A 2-tuple of ``(expanded_items, closures_added)``.  *closures_added*
        counts how many new items were inserted.
    """
    present_ids = {item.id for item in items}
    extra: list[ContextItem] = []
    closures = 0

    for item in list(items):
        parent_id = item.parent_id
        while parent_id is not None:
            if parent_id in present_ids:
                break
            try:
                parent = event_log.get(parent_id)
                extra.append(parent)
                present_ids.add(parent_id)
                closures += 1
                parent_id = parent.parent_id
            except Exception:
                break

    # Preserve log order by re-sorting via original indices
    all_ids = {item.id: item for item in items + extra}
    ordered = [item for item in event_log.all() if item.id in all_ids]
    return ordered, closures

"""Candidate generation for the contextweaver Context Engine (Stage 1).

Filters raw event log items into phase-eligible candidates.
"""

from __future__ import annotations

import time

from contextweaver.config import ContextPolicy
from contextweaver.types import ContextItem, Phase, Sensitivity


def generate_candidates(
    event_log_items: list[ContextItem],
    phase: Phase,
    policy: ContextPolicy,
) -> list[ContextItem]:
    """Filter raw event log items into phase-eligible candidates.

    Filters: allowed kinds for phase, sensitivity floor, TTL expiry.
    Applies redaction hooks: hook.redact(item) -> None means drop.
    Returns filtered list (order preserved).
    """
    allowed = set(policy.allowed_kinds_per_phase.get(phase, []))
    now = time.time()
    result: list[ContextItem] = []

    for item in event_log_items:
        # Kind filter
        if item.kind not in allowed:
            continue

        # Sensitivity filter
        sensitivity_str = item.metadata.get("sensitivity", "public")
        try:
            item_sensitivity = Sensitivity(sensitivity_str)
        except ValueError:
            item_sensitivity = Sensitivity.PUBLIC

        sensitivity_order = [
            Sensitivity.PUBLIC,
            Sensitivity.INTERNAL,
            Sensitivity.CONFIDENTIAL,
            Sensitivity.RESTRICTED,
        ]
        floor_idx = sensitivity_order.index(policy.sensitivity_floor)
        item_idx = sensitivity_order.index(item_sensitivity)
        if item_idx > floor_idx:
            continue

        # TTL check
        ttl = item.metadata.get("ttl_seconds")
        if ttl is not None and policy.ttl_behavior == "hard_drop":
            ts = item.metadata.get("timestamp", 0.0)
            if ts + ttl < now:
                continue

        # Redaction hooks
        current: ContextItem | None = item
        for hook in policy.redaction_hooks:
            if current is None:
                break
            current = hook.redact(current)

        if current is not None:
            result.append(current)

    return result

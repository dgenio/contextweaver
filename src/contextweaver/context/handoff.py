"""Session handoff context pack builder and Markdown renderer.

The builder snapshots durable session context from the existing event log,
filters sensitivity before classification, runs the context firewall over
``tool_result`` items, and returns a deterministic, budget-aware pack.
"""

from __future__ import annotations

import logging

from contextweaver.config import ContextPolicy
from contextweaver.context.firewall import apply_firewall_to_batch
from contextweaver.context.handoff_types import (
    HANDOFF_CATEGORIES,
    HANDOFF_PACK_VERSION,
    HandoffEntry,
    SessionHandoffPack,
    category_attr,
)
from contextweaver.context.sensitivity import apply_sensitivity_filter
from contextweaver.protocols import ArtifactStore, EventLog, TokenEstimator
from contextweaver.types import ArtifactRef, ContextItem, ItemKind

logger = logging.getLogger("contextweaver.context")

_HEURISTIC_CONFIDENCE = 0.5
_EXPLICIT_CONFIDENCE = 1.0


def _classify(item: ContextItem) -> tuple[str | None, float]:
    """Classify *item* into one of :data:`HANDOFF_CATEGORIES`."""
    explicit = item.metadata.get("handoff_category")
    if isinstance(explicit, str) and explicit in HANDOFF_CATEGORIES:
        return explicit, _EXPLICIT_CONFIDENCE
    if item.kind == ItemKind.plan_state:
        return "decision", _HEURISTIC_CONFIDENCE
    if item.kind == ItemKind.policy:
        return "convention", _HEURISTIC_CONFIDENCE
    if item.kind == ItemKind.tool_result:
        status = item.metadata.get("status")
        if isinstance(status, str) and status.lower() in {"failed", "error"}:
            return "pitfall", _HEURISTIC_CONFIDENCE
    return None, 0.0


def _ancestor_artifacts(
    item: ContextItem,
    artifact_store: ArtifactStore,
    seen_handles: set[str],
    processed_by_id: dict[str, ContextItem],
) -> list[ArtifactRef]:
    """Collect artifact refs from *item* through sensitivity-surviving parents."""
    out: list[ArtifactRef] = []
    cursor: ContextItem | None = item
    visited: set[str] = set()
    while cursor is not None and cursor.id not in visited:
        visited.add(cursor.id)
        if cursor.artifact_ref is not None and cursor.artifact_ref.handle not in seen_handles:
            try:
                ref = artifact_store.ref(cursor.artifact_ref.handle)
            except Exception:  # noqa: BLE001 - artifact lookups are best-effort
                ref = cursor.artifact_ref
            seen_handles.add(ref.handle)
            out.append(ref)
        if cursor.parent_id is None:
            break
        cursor = processed_by_id.get(cursor.parent_id)
    return out


def _positive_cost(text: str, estimator: TokenEstimator) -> int:
    return max(1, int(estimator.estimate(text)))


def build_session_handoff_pack(
    event_log: EventLog,
    artifact_store: ArtifactStore,
    policy: ContextPolicy,
    estimator: TokenEstimator,
    *,
    budget_tokens: int = 1500,
) -> SessionHandoffPack:
    """Build a :class:`SessionHandoffPack` from the current session state.

    Pipeline:
    1. Read every item from *event_log*.
    2. Drop or redact sensitive items via :func:`apply_sensitivity_filter`.
    3. Run :func:`apply_firewall_to_batch` so raw ``tool_result`` text is
       replaced before classification and rendering.
    4. Classify surviving items into handoff buckets.
    5. Greedily fill buckets up to *budget_tokens*.
    6. Walk parent chains and collect deduplicated artifact references.

    Args:
        event_log: Source event log.
        artifact_store: Used to look up canonical artefact metadata.
        policy: Active context policy for sensitivity enforcement.
        estimator: Token estimator used for the cumulative budget.
        budget_tokens: Hard cap on included entry text tokens.

    Returns:
        A populated deterministic handoff pack.
    """
    raw_items = event_log.all()
    filtered, dropped = apply_sensitivity_filter(raw_items, policy)
    firewalled, _ = apply_firewall_to_batch(filtered, artifact_store)
    processed_by_id = {item.id: item for item in firewalled}

    candidates: list[tuple[str, float, int, ContextItem]] = []
    for log_index, item in enumerate(firewalled):
        category, confidence = _classify(item)
        if category is not None:
            candidates.append((category, confidence, log_index, item))
    candidates.sort(key=lambda triple: (-triple[1], triple[2]))

    pack = SessionHandoffPack(sensitivity_dropped=dropped)
    seen_handles: set[str] = set()
    remaining = max(0, int(budget_tokens))

    for category, confidence, _, item in candidates:
        cost = _positive_cost(item.text, estimator)
        if cost > remaining:
            continue
        entry = HandoffEntry(
            id=item.id,
            text=item.text,
            category=category,
            source_ids=[item.id],
            confidence=confidence,
            token_estimate=cost,
        )
        bucket: list[HandoffEntry] = getattr(pack, category_attr(category))
        bucket.append(entry)
        pack.token_estimate += cost
        remaining -= cost
        pack.artifact_refs.extend(
            _ancestor_artifacts(item, artifact_store, seen_handles, processed_by_id)
        )

    logger.debug(
        "handoff_pack: included=%d, sensitivity_dropped=%d, tokens=%d/%d, artifacts=%d",
        sum(len(getattr(pack, category_attr(c))) for c in HANDOFF_CATEGORIES),
        pack.sensitivity_dropped,
        pack.token_estimate,
        budget_tokens,
        len(pack.artifact_refs),
    )
    return pack


_CATEGORY_HEADINGS: dict[str, str] = {
    "decision": "## Decisions",
    "convention": "## Conventions",
    "unresolved": "## Unresolved tasks",
    "pitfall": "## Pitfalls",
    "next_step": "## Next inspection points",
}


def render_handoff_pack(pack: SessionHandoffPack) -> str:
    """Render *pack* as deterministic Markdown safe for a session prelude.

    Args:
        pack: The pack to render.

    Returns:
        Markdown text ending with a trailing newline.
    """
    parts: list[str] = [f"# Session handoff (v{pack.version})"]
    for category in HANDOFF_CATEGORIES:
        bucket: list[HandoffEntry] = getattr(pack, category_attr(category))
        if not bucket:
            continue
        parts.append(_CATEGORY_HEADINGS[category])
        for entry in bucket:
            parts.append(f"- ({entry.id}) {entry.text}")
    if pack.artifact_refs:
        parts.append("## Cited artefacts")
        for ref in pack.artifact_refs:
            label = ref.label or ref.handle
            parts.append(f"- {ref.handle}: {label} [{ref.media_type}, {ref.size_bytes}B]")
    return "\n".join(parts) + "\n"


__all__ = [
    "HANDOFF_CATEGORIES",
    "HANDOFF_PACK_VERSION",
    "HandoffEntry",
    "SessionHandoffPack",
    "build_session_handoff_pack",
    "render_handoff_pack",
]

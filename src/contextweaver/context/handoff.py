"""Session handoff context pack for agent continuity.

This module ships a deterministic, budget-aware way to snapshot the
durable parts of a session — decisions, repo conventions, unresolved
tasks, dependency-linked artefact references, known pitfalls, and
recommended next-inspection points — so the next session boots without
re-stating them.

The pack is built from the existing event log and artifact store; no
new pipeline stage is introduced and no invariant changes.  Sensitive
content (items at or above the active
:attr:`~contextweaver.config.ContextPolicy.sensitivity_floor`) is
filtered out via the existing
:func:`~contextweaver.context.sensitivity.apply_sensitivity_filter`
*before* classification, so the handoff pack cannot leak a redacted
item's raw text even when callers ask for ``"redact"`` action mode.

Classification is metadata-driven and deterministic:

* ``metadata['handoff_category']`` on an event-log item — explicit
  one-of {``decision``, ``convention``, ``unresolved``, ``pitfall``,
  ``next_step``}.
* Heuristic fallback for items that do not carry a category:
  ``ItemKind.plan_state`` → ``decision``; ``ItemKind.policy`` →
  ``convention``; ``ItemKind.tool_result`` whose ``metadata['status']``
  is in {``"failed"``, ``"error"``} → ``pitfall``.

Issue #294.  Pairs naturally with #293 (memory-source adapter); a
future :class:`~contextweaver.protocols.MemorySource` backend can ingest
:class:`SessionHandoffPack` payloads back into the next session.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from contextweaver.config import ContextPolicy
from contextweaver.context.sensitivity import apply_sensitivity_filter
from contextweaver.protocols import ArtifactStore, EventLog, TokenEstimator
from contextweaver.types import ArtifactRef, ContextItem, ItemKind

logger = logging.getLogger("contextweaver.context")


#: Schema version for the :class:`SessionHandoffPack` payload.  Bump when the
#: dict shape changes in a way that would break older readers; the field is
#: written into ``to_dict()`` output so downstream consumers can detect drift.
HANDOFF_PACK_VERSION = "1"


#: The five canonical handoff buckets — order is significant because
#: deterministic rendering walks them in this order.
HANDOFF_CATEGORIES: tuple[str, ...] = (
    "decision",
    "convention",
    "unresolved",
    "pitfall",
    "next_step",
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class HandoffEntry:
    """A single classified handoff item.

    Attributes:
        id: Stable identifier — by convention the source
            :class:`~contextweaver.types.ContextItem` ``id`` so consumers
            can trace back to the original log entry.
        text: The entry text (post-redaction).
        category: One of :data:`HANDOFF_CATEGORIES`.
        source_ids: IDs of the event-log items this entry cites.  Empty
            unless the entry aggregates multiple items.
        confidence: ``0.0``–``1.0`` classification confidence — ``1.0``
            for explicitly-tagged entries, ``0.5`` for heuristic matches.
        token_estimate: Cached token cost of ``text``.
    """

    id: str
    text: str
    category: str
    source_ids: list[str] = field(default_factory=list)
    confidence: float = 1.0
    token_estimate: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "id": self.id,
            "text": self.text,
            "category": self.category,
            "source_ids": list(self.source_ids),
            "confidence": self.confidence,
            "token_estimate": self.token_estimate,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HandoffEntry:
        """Deserialise from a JSON-compatible dict produced by :meth:`to_dict`."""
        return cls(
            id=str(data["id"]),
            text=str(data["text"]),
            category=str(data["category"]),
            source_ids=list(data.get("source_ids", [])),
            confidence=float(data.get("confidence", 1.0)),
            token_estimate=int(data.get("token_estimate", 0)),
        )


@dataclass
class SessionHandoffPack:
    """Compact, safe-to-inject snapshot of a session for the next agent boot.

    Attributes:
        decisions: Durable architecture / approach decisions.
        conventions: Repo / project conventions the next session must respect.
        unresolved_tasks: Open TODOs the prior session deferred.
        pitfalls: Known footguns / things that went wrong.
        next_inspections: Recommended files / artefacts the next session
            should look at first.
        artifact_refs: Artefact references cited by any included entry,
            preserving dependency-closure semantics.
        sensitivity_dropped: Count of items removed by the sensitivity filter
            during construction.  Useful for surfacing in logs and tests.
        token_estimate: Cumulative token cost of all included entry texts.
        version: Schema version tag (:data:`HANDOFF_PACK_VERSION`).
    """

    decisions: list[HandoffEntry] = field(default_factory=list)
    conventions: list[HandoffEntry] = field(default_factory=list)
    unresolved_tasks: list[HandoffEntry] = field(default_factory=list)
    pitfalls: list[HandoffEntry] = field(default_factory=list)
    next_inspections: list[HandoffEntry] = field(default_factory=list)
    artifact_refs: list[ArtifactRef] = field(default_factory=list)
    sensitivity_dropped: int = 0
    token_estimate: int = 0
    version: str = HANDOFF_PACK_VERSION

    def all_entries(self) -> list[HandoffEntry]:
        """Return all entries flattened in canonical category order."""
        return [
            *self.decisions,
            *self.conventions,
            *self.unresolved_tasks,
            *self.pitfalls,
            *self.next_inspections,
        ]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "version": self.version,
            "decisions": [e.to_dict() for e in self.decisions],
            "conventions": [e.to_dict() for e in self.conventions],
            "unresolved_tasks": [e.to_dict() for e in self.unresolved_tasks],
            "pitfalls": [e.to_dict() for e in self.pitfalls],
            "next_inspections": [e.to_dict() for e in self.next_inspections],
            "artifact_refs": [a.to_dict() for a in self.artifact_refs],
            "sensitivity_dropped": self.sensitivity_dropped,
            "token_estimate": self.token_estimate,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionHandoffPack:
        """Deserialise from a JSON-compatible dict produced by :meth:`to_dict`."""
        return cls(
            decisions=[HandoffEntry.from_dict(d) for d in data.get("decisions", [])],
            conventions=[HandoffEntry.from_dict(d) for d in data.get("conventions", [])],
            unresolved_tasks=[HandoffEntry.from_dict(d) for d in data.get("unresolved_tasks", [])],
            pitfalls=[HandoffEntry.from_dict(d) for d in data.get("pitfalls", [])],
            next_inspections=[HandoffEntry.from_dict(d) for d in data.get("next_inspections", [])],
            artifact_refs=[ArtifactRef.from_dict(a) for a in data.get("artifact_refs", [])],
            sensitivity_dropped=int(data.get("sensitivity_dropped", 0)),
            token_estimate=int(data.get("token_estimate", 0)),
            version=str(data.get("version", HANDOFF_PACK_VERSION)),
        )


# ---------------------------------------------------------------------------
# Classification + building
# ---------------------------------------------------------------------------


_HEURISTIC_CONFIDENCE = 0.5
_EXPLICIT_CONFIDENCE = 1.0


def _classify(item: ContextItem) -> tuple[str | None, float]:
    """Classify *item* into one of :data:`HANDOFF_CATEGORIES`.

    Returns:
        A ``(category, confidence)`` tuple.  ``(None, 0.0)`` when the item
        does not belong in any handoff bucket.
    """
    explicit = item.metadata.get("handoff_category")
    if isinstance(explicit, str) and explicit in HANDOFF_CATEGORIES:
        return explicit, _EXPLICIT_CONFIDENCE
    # Heuristic fallbacks — only for items that look durable.
    if item.kind == ItemKind.plan_state:
        return "decision", _HEURISTIC_CONFIDENCE
    if item.kind == ItemKind.policy:
        return "convention", _HEURISTIC_CONFIDENCE
    if item.kind == ItemKind.tool_result:
        status = item.metadata.get("status")
        if isinstance(status, str) and status.lower() in {"failed", "error"}:
            return "pitfall", _HEURISTIC_CONFIDENCE
    return None, 0.0


def _category_attr(category: str) -> str:
    """Map a category name to its :class:`SessionHandoffPack` field name."""
    return {
        "decision": "decisions",
        "convention": "conventions",
        "unresolved": "unresolved_tasks",
        "pitfall": "pitfalls",
        "next_step": "next_inspections",
    }[category]


def _ancestor_artifacts(
    item: ContextItem,
    event_log: EventLog,
    artifact_store: ArtifactStore,
    seen_handles: set[str],
) -> list[ArtifactRef]:
    """Collect every ``ArtifactRef`` reachable from *item* via ``parent_id``.

    Walks the parent chain and pulls the artifact ref off any ancestor item
    that has one.  Refs already present in *seen_handles* are skipped so
    the resulting list stays deduplicated across siblings.
    """
    out: list[ArtifactRef] = []
    cursor: ContextItem | None = item
    visited: set[str] = set()
    while cursor is not None and cursor.id not in visited:
        visited.add(cursor.id)
        if cursor.artifact_ref is not None and cursor.artifact_ref.handle not in seen_handles:
            try:
                ref = artifact_store.ref(cursor.artifact_ref.handle)
            except Exception:  # noqa: BLE001 — artifact lookups are best-effort
                ref = cursor.artifact_ref
            seen_handles.add(ref.handle)
            out.append(ref)
        if cursor.parent_id is None:
            break
        cursor = event_log.parent(cursor.id)
    return out


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
    2. Drop sensitive items via :func:`apply_sensitivity_filter` using the
       supplied *policy* — this guarantees secrets / restricted content
       cannot survive to the produced pack, even when the policy uses the
       ``"redact"`` action (in which case the entry's redacted placeholder
       text is what lands in the pack).
    3. Classify surviving items with :func:`_classify`.
    4. Greedily fill each bucket up to *budget_tokens* cumulative cost,
       sorting candidates by (descending confidence → log order).
    5. Walk each kept item's ``parent_id`` chain and collect deduplicated
       :class:`~contextweaver.types.ArtifactRef` objects for dependency
       closure.

    Args:
        event_log: Source event log.
        artifact_store: Used to look up canonical artefact metadata when an
            event-log item carries an :class:`ArtifactRef`.
        policy: Active context policy; supplies the sensitivity floor /
            action used to filter items before classification.
        estimator: Token estimator used for the cumulative budget.
        budget_tokens: Hard cap on the combined ``token_estimate`` of every
            included :class:`HandoffEntry`.  Defaults to ``1500`` tokens —
            enough for ~50–100 short entries with the default
            ``CharDivFourEstimator``.

    Returns:
        A populated :class:`SessionHandoffPack`.  The returned pack is
        deterministic for fixed inputs.
    """
    raw_items = event_log.all()
    filtered, dropped = apply_sensitivity_filter(raw_items, policy)

    # Build classification candidates.
    candidates: list[tuple[str, float, int, ContextItem]] = []
    for log_index, item in enumerate(filtered):
        category, confidence = _classify(item)
        if category is None:
            continue
        candidates.append((category, confidence, log_index, item))

    # Sort: explicit-confidence first, then log order (stable for ties).
    candidates.sort(key=lambda triple: (-triple[1], triple[2]))

    pack = SessionHandoffPack(sensitivity_dropped=dropped)
    seen_handles: set[str] = set()
    remaining = max(0, int(budget_tokens))

    for category, confidence, _, item in candidates:
        text = item.text
        cost = estimator.estimate(text)
        if cost <= 0:
            cost = max(1, len(text) // 4)
        if cost > remaining:
            continue
        entry = HandoffEntry(
            id=item.id,
            text=text,
            category=category,
            source_ids=[item.id],
            confidence=confidence,
            token_estimate=cost,
        )
        bucket: list[HandoffEntry] = getattr(pack, _category_attr(category))
        bucket.append(entry)
        pack.token_estimate += cost
        remaining -= cost
        pack.artifact_refs.extend(
            _ancestor_artifacts(item, event_log, artifact_store, seen_handles)
        )

    logger.debug(
        "handoff_pack: included=%d, sensitivity_dropped=%d, tokens=%d/%d, artifacts=%d",
        sum(len(getattr(pack, _category_attr(c))) for c in HANDOFF_CATEGORIES),
        pack.sensitivity_dropped,
        pack.token_estimate,
        budget_tokens,
        len(pack.artifact_refs),
    )
    return pack


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


_CATEGORY_HEADINGS: dict[str, str] = {
    "decision": "## Decisions",
    "convention": "## Conventions",
    "unresolved": "## Unresolved tasks",
    "pitfall": "## Pitfalls",
    "next_step": "## Next inspection points",
}


def render_handoff_pack(pack: SessionHandoffPack) -> str:
    """Render *pack* as deterministic Markdown safe to inject as a session prelude.

    The output walks :data:`HANDOFF_CATEGORIES` in canonical order, emits a
    heading per non-empty bucket, lists entries as bullet points using each
    entry's ``text``, and appends an artefact-refs appendix when any
    :class:`ArtifactRef` survived dependency closure.  Empty buckets are
    omitted so the prelude stays compact.

    Args:
        pack: The pack to render.

    Returns:
        A Markdown string.  Always ends with a trailing newline.
    """
    parts: list[str] = [f"# Session handoff (v{pack.version})"]
    for category in HANDOFF_CATEGORIES:
        bucket: list[HandoffEntry] = getattr(pack, _category_attr(category))
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

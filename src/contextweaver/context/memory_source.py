"""Memory-source adapter interface for phase-aware context compilation.

This module defines the :class:`MemoryEntry` dataclass and a stdlib-only
:class:`JsonFixtureMemorySource` that emits memory records as candidate
:class:`~contextweaver.types.ContextItem` objects of kind
:attr:`~contextweaver.types.ItemKind.memory_fact`.

Once materialised via :func:`memory_entries_to_context_items`, memory entries
flow through the existing Context Engine pipeline unchanged: phase filtering
(:func:`~contextweaver.context.candidates.generate_candidates`), sensitivity
enforcement (:func:`~contextweaver.context.sensitivity.apply_sensitivity_filter`),
scoring, deduplication, and budget selection all apply without modification.

The :class:`~contextweaver.protocols.MemorySource` Protocol — the abstract
interface — lives in :mod:`contextweaver.protocols` next to the other
runtime-checkable protocols.  This module ships:

* :class:`MemoryEntry` — the canonical dataclass.
* :class:`JsonFixtureMemorySource` — a deterministic in-memory adapter
  suitable for fixtures, tests, and JSON-backed local memory.
* :func:`memory_entries_to_context_items` — conversion helper that respects
  ``expires_at`` and stamps the
  ``metadata['_contextweaver']['memory_source']`` provenance namespace.
* :func:`select_memory_for_phase` — convenience that wires a source through
  the existing budget / estimator plumbing.

Issue #293.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from contextweaver.exceptions import ConfigError
from contextweaver.protocols import MemorySource, TokenEstimator
from contextweaver.types import ContextItem, ItemKind, Phase, Sensitivity

logger = logging.getLogger("contextweaver.context")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


#: Phase → preferred memory ``scope`` tags.  Entries whose ``scope`` matches
#: one of the listed tags are ranked above entries whose ``scope`` is empty
#: or unrelated.  Used by :class:`JsonFixtureMemorySource` and
#: :func:`select_memory_for_phase`; custom backends are free to define their
#: own mapping.
PHASE_SCOPE_PREFERENCES: dict[Phase, tuple[str, ...]] = {
    Phase.route: ("routing", "tool_preference", "policy"),
    Phase.call: ("tool_usage", "tool_preference", "domain"),
    Phase.interpret: ("domain", "fact", "convention"),
    Phase.answer: ("domain", "fact", "convention", "preference"),
}


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class MemoryEntry:
    """A single memory record sourced from a long-lived backend or fixture.

    Attributes:
        id: Stable, source-unique identifier.  Used for tie-breaking and for
            deduplication when the same backend is wired up twice.
        text: The memory content itself.  Treated as candidate text by the
            Context Engine; subject to the firewall only if the entry is
            promoted to a ``tool_result`` kind by a custom adapter.
        source: Free-form provenance label (e.g. ``"mem0"``, ``"fixture"``,
            ``"design-doc"``).  Surfaced in
            ``metadata['_contextweaver']['memory_source']``.
        timestamp: UNIX seconds when the memory was recorded.  Used for
            recency biasing.  ``0.0`` means "unknown" — such entries rank
            below dated entries on ties.
        scope: Short tag describing what the memory is *about* (e.g.
            ``"routing"``, ``"tool_usage"``, ``"domain"``, ``"convention"``).
            Phase selection consults
            :data:`PHASE_SCOPE_PREFERENCES` to decide which entries are
            relevant for which phase.
        sensitivity: Per-entry sensitivity level.  Defaults to
            :attr:`~contextweaver.types.Sensitivity.public`; entries at or
            above the active
            :attr:`~contextweaver.config.ContextPolicy.sensitivity_floor`
            are dropped or redacted by the existing pipeline stage.
        confidence: ``0.0``–``1.0`` confidence score.  Higher confidence
            ranks first; ties broken by recency then by ID.
        expires_at: Optional UNIX seconds after which the entry must be
            dropped from any new context build.  ``None`` means no expiry.
        tags: Optional list of tags forwarded to the resulting
            :class:`~contextweaver.types.ContextItem` ``metadata['tags']``
            for tag-match scoring.
        metadata: Free-form metadata; merged into the produced
            :class:`ContextItem`'s metadata under the entry's own key set,
            then namespaced under ``_contextweaver.memory_source``.
    """

    id: str
    text: str
    source: str = ""
    timestamp: float = 0.0
    scope: str = ""
    sensitivity: Sensitivity = Sensitivity.public
    confidence: float = 1.0
    expires_at: float | None = None
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "id": self.id,
            "text": self.text,
            "source": self.source,
            "timestamp": self.timestamp,
            "scope": self.scope,
            "sensitivity": self.sensitivity.value,
            "confidence": self.confidence,
            "expires_at": self.expires_at,
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryEntry:
        """Deserialise from a JSON-compatible dict produced by :meth:`to_dict`."""
        _d = cls(id=str(data["id"]), text=str(data["text"]))
        sensitivity_raw = data.get("sensitivity", _d.sensitivity.value)
        return cls(
            id=str(data["id"]),
            text=str(data["text"]),
            source=str(data.get("source", _d.source)),
            timestamp=float(data.get("timestamp", _d.timestamp)),
            scope=str(data.get("scope", _d.scope)),
            sensitivity=Sensitivity(sensitivity_raw),
            confidence=float(data.get("confidence", _d.confidence)),
            expires_at=(None if data.get("expires_at") is None else float(data["expires_at"])),
            tags=list(data.get("tags", [])),
            metadata=dict(data.get("metadata", {})),
        )

    def is_expired(self, *, now: float | None = None) -> bool:
        """Return ``True`` if the entry has passed its ``expires_at`` boundary.

        Args:
            now: UNIX seconds reference time.  ``None`` falls back to
                :func:`time.time`; pin this in tests for determinism.
        """
        if self.expires_at is None:
            return False
        reference = time.time() if now is None else now
        return reference >= self.expires_at


# ---------------------------------------------------------------------------
# Stdlib fixture adapter
# ---------------------------------------------------------------------------


def _entry_score(entry: MemoryEntry, query_tokens: set[str], scope_bonus: float) -> float:
    """Return a deterministic relevance score for *entry*.

    The score blends three signals — token overlap with the query, the
    entry's own ``confidence``, and a phase-scope bonus — so the result is
    bounded in ``[0.0, 3.0]`` and stable across machines.
    """
    if query_tokens:
        text_tokens = {tok for tok in entry.text.lower().split() if tok}
        tag_tokens = {tag.lower() for tag in entry.tags}
        overlap_count = len(query_tokens & (text_tokens | tag_tokens))
        overlap = overlap_count / max(len(query_tokens), 1)
    else:
        overlap = 0.0
    confidence = max(0.0, min(1.0, entry.confidence))
    return overlap + confidence + scope_bonus


def _query_tokens(query: str) -> set[str]:
    return {tok for tok in query.lower().split() if tok}


def _scope_bonus(entry_scope: str, phase_scopes: tuple[str, ...]) -> float:
    """Return a position-graded scope bonus in ``[0.0, 1.0]``.

    The first scope listed for the phase gets the largest bonus (``1.0``);
    later scopes are linearly discounted; unlisted scopes get ``0.0``.  This
    lets each phase express a preference *ordering*, not just a yes/no
    membership — e.g. ``Phase.call`` ranks ``"tool_usage"`` strictly above
    ``"domain"`` even when both are listed as relevant.
    """
    if not phase_scopes or not entry_scope:
        return 0.0
    try:
        index = phase_scopes.index(entry_scope)
    except ValueError:
        return 0.0
    return (len(phase_scopes) - index) / len(phase_scopes)


class JsonFixtureMemorySource:
    """Deterministic in-memory :class:`~contextweaver.protocols.MemorySource`.

    Holds a list of :class:`MemoryEntry` objects and selects them by phase
    using :data:`PHASE_SCOPE_PREFERENCES`.  No persistent storage, no
    network, no background processing — suitable for fixtures, tests, and
    JSON-backed local memory.

    Args:
        entries: Initial memory entries.  May be empty.
        phase_scopes: Optional override of :data:`PHASE_SCOPE_PREFERENCES`
            for callers who want to bias selection differently.

    The class is intentionally tiny: external backends should implement
    :class:`~contextweaver.protocols.MemorySource` directly rather than
    subclass this one.
    """

    def __init__(
        self,
        entries: list[MemoryEntry] | None = None,
        *,
        phase_scopes: dict[Phase, tuple[str, ...]] | None = None,
    ) -> None:
        self._entries: list[MemoryEntry] = list(entries or [])
        self._phase_scopes: dict[Phase, tuple[str, ...]] = dict(
            phase_scopes or PHASE_SCOPE_PREFERENCES
        )
        self._seen: set[str] = {e.id for e in self._entries}
        if len(self._seen) != len(self._entries):
            msg = "JsonFixtureMemorySource: duplicate MemoryEntry.id values are not allowed"
            raise ConfigError(msg)

    @classmethod
    def from_json_file(cls, path: str | Path) -> JsonFixtureMemorySource:
        """Load entries from a JSON file containing a list of dicts.

        The file must contain a JSON array; each element must be a dict
        accepted by :meth:`MemoryEntry.from_dict`.  Trailing whitespace and
        UTF-8 BOM are tolerated.

        Args:
            path: Filesystem path to the JSON fixture.

        Raises:
            ConfigError: If the file is missing, unreadable, or contains a
                non-list payload.
        """
        p = Path(path)
        if not p.is_file():
            msg = f"JsonFixtureMemorySource: fixture file not found: {p}"
            raise ConfigError(msg)
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            msg = f"JsonFixtureMemorySource: invalid JSON in {p}: {exc}"
            raise ConfigError(msg) from exc
        if not isinstance(data, list):
            msg = f"JsonFixtureMemorySource: expected a JSON list in {p}, got {type(data).__name__}"
            raise ConfigError(msg)
        return cls([MemoryEntry.from_dict(raw) for raw in data])

    def add(self, entry: MemoryEntry) -> None:
        """Append *entry* to the source.

        Raises:
            ConfigError: If an entry with the same ``id`` already exists.
        """
        if entry.id in self._seen:
            msg = f"JsonFixtureMemorySource: duplicate entry id {entry.id!r}"
            raise ConfigError(msg)
        self._entries.append(entry)
        self._seen.add(entry.id)

    def all(self) -> list[MemoryEntry]:
        """Return all stored entries in insertion order."""
        return list(self._entries)

    def select(
        self,
        query: str,
        phase: Phase,
        *,
        now: float | None = None,
        max_entries: int | None = None,
    ) -> list[MemoryEntry]:
        """Return entries relevant to *query* under *phase*.

        Honours :class:`~contextweaver.protocols.MemorySource`'s
        determinism contract: identical inputs against an unchanged
        backend yield identical output ordering.  Tie-break order is
        (highest score → most recent ``timestamp`` → smallest ``id``
        lexicographically).
        """
        scopes = self._phase_scopes.get(phase, ())
        tokens = _query_tokens(query)
        ranked: list[tuple[float, float, str, MemoryEntry]] = []
        for entry in self._entries:
            if entry.is_expired(now=now):
                continue
            bonus = _scope_bonus(entry.scope, scopes)
            score = _entry_score(entry, tokens, bonus)
            # Negate score + timestamp so that sorted() ascending yields the
            # desired (high score, recent first) ordering; ID then breaks ties
            # in lexicographic ascending order.
            ranked.append((-score, -entry.timestamp, entry.id, entry))
        ranked.sort()
        chosen = [e for _, _, _, e in ranked]
        if max_entries is not None and max_entries >= 0:
            chosen = chosen[:max_entries]
        logger.debug(
            "memory_source.select: phase=%s, query_tokens=%d, returned=%d",
            phase.value,
            len(tokens),
            len(chosen),
        )
        return chosen


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def memory_entries_to_context_items(
    entries: list[MemoryEntry],
    *,
    estimator: TokenEstimator | None = None,
    now: float | None = None,
) -> list[ContextItem]:
    """Materialise *entries* into :class:`ContextItem` candidates.

    Expired entries are filtered out.  Each surviving entry becomes a
    :class:`ContextItem` of kind
    :attr:`~contextweaver.types.ItemKind.memory_fact`; the entry's sensitivity
    level is preserved verbatim and enforced downstream by the existing
    :func:`~contextweaver.context.sensitivity.apply_sensitivity_filter`.

    Args:
        entries: Source entries.
        estimator: Optional :class:`~contextweaver.protocols.TokenEstimator`.
            When omitted, ``len(text) // 4`` is used to mirror the firewall's
            cheap default.
        now: UNIX seconds reference time for expiry filtering; ``None`` falls
            back to :func:`time.time`.

    Returns:
        A list of :class:`ContextItem` objects in the same order as *entries*.
    """
    result: list[ContextItem] = []
    for entry in entries:
        if entry.is_expired(now=now):
            continue
        token_estimate = (
            estimator.estimate(entry.text) if estimator is not None else len(entry.text) // 4
        )
        merged_metadata: dict[str, Any] = dict(entry.metadata)
        if entry.tags:
            merged_metadata.setdefault("tags", list(entry.tags))
        # Reserved provenance namespace per invariants.md.
        cw_ns = dict(merged_metadata.get("_contextweaver", {}))
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
                token_estimate=token_estimate,
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

    The function is the recommended entry point for ad-hoc memory injection:
    it calls :meth:`MemorySource.select`, materialises via
    :func:`memory_entries_to_context_items`, and then truncates greedily so
    that the cumulative ``token_estimate`` does not exceed *budget_tokens*.
    Entries that individually exceed the remaining budget are skipped, but
    smaller subsequent entries can still be packed.

    Args:
        source: Any :class:`~contextweaver.protocols.MemorySource`
            implementation; structural typing means any object exposing the
            ``select`` method is accepted at runtime.
        query: Selection query.
        phase: Active execution phase.
        budget_tokens: Hard cap on the cumulative
            :attr:`~contextweaver.types.ContextItem.token_estimate` of the
            returned items.  ``0`` returns an empty list.
        estimator: Optional :class:`~contextweaver.protocols.TokenEstimator`;
            same default as :func:`memory_entries_to_context_items`.
        now: UNIX seconds reference time for expiry filtering.
        max_entries: Optional hard cap forwarded to
            :meth:`MemorySource.select` before budgeting.

    Returns:
        A list of :class:`ContextItem` objects that fit within
        *budget_tokens*, preserving the relevance order returned by the
        source.
    """
    if budget_tokens <= 0:
        return []
    entries = source.select(query, phase, now=now, max_entries=max_entries)
    items = memory_entries_to_context_items(entries, estimator=estimator, now=now)
    packed: list[ContextItem] = []
    remaining = budget_tokens
    for item in items:
        cost = item.token_estimate
        if cost <= 0:
            packed.append(item)
            continue
        if cost > remaining:
            # Skip but keep budgeting — a smaller later item may still fit.
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

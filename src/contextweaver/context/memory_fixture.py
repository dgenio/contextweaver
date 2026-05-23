"""Stdlib fixture implementation of the memory-source protocol."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from contextweaver.context.memory_types import PHASE_SCOPE_PREFERENCES, MemoryEntry
from contextweaver.exceptions import ConfigError
from contextweaver.types import Phase

logger = logging.getLogger("contextweaver.context")


def _entry_score(entry: MemoryEntry, query_tokens: set[str], scope_bonus: float) -> float:
    """Return a deterministic relevance score for *entry*."""
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
    """Return a position-graded scope bonus in ``[0.0, 1.0]``."""
    if not phase_scopes or not entry_scope:
        return 0.0
    try:
        index = phase_scopes.index(entry_scope)
    except ValueError:
        return 0.0
    return (len(phase_scopes) - index) / len(phase_scopes)


class JsonFixtureMemorySource:
    """Deterministic in-memory memory source for fixtures and tests.

    Args:
        entries: Initial memory entries. May be empty.
        phase_scopes: Optional override of :data:`PHASE_SCOPE_PREFERENCES`.
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

        Raises:
            ConfigError: If the file is missing, unreadable, malformed, or
                contains a non-list payload.
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
        """Return entries relevant to *query* under *phase*."""
        scopes = self._phase_scopes.get(phase, ())
        tokens = _query_tokens(query)
        ranked: list[tuple[float, float, str, MemoryEntry]] = []
        for entry in self._entries:
            if entry.is_expired(now=now):
                continue
            bonus = _scope_bonus(entry.scope, scopes)
            score = _entry_score(entry, tokens, bonus)
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


__all__ = ["JsonFixtureMemorySource"]

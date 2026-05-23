"""Dataclasses and constants for memory-source ingestion."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from contextweaver.exceptions import ConfigError
from contextweaver.types import Phase, Sensitivity

#: Phase -> preferred memory ``scope`` tags.  Earlier tags rank higher.
PHASE_SCOPE_PREFERENCES: dict[Phase, tuple[str, ...]] = {
    Phase.route: ("routing", "tool_preference", "policy"),
    Phase.call: ("tool_usage", "tool_preference", "domain"),
    Phase.interpret: ("domain", "fact", "convention"),
    Phase.answer: ("domain", "fact", "convention", "preference"),
}


@dataclass
class MemoryEntry:
    """A single memory record sourced from a long-lived backend or fixture.

    Attributes:
        id: Stable, source-unique identifier.
        text: The memory content itself.
        source: Free-form provenance label.
        timestamp: UNIX seconds when the memory was recorded.
        scope: Short tag describing what the memory is about.
        sensitivity: Per-entry sensitivity level.
        confidence: ``0.0``-``1.0`` confidence score.
        expires_at: Optional UNIX seconds after which the entry is dropped.
        tags: Optional tags forwarded to the resulting context item metadata.
        metadata: Free-form metadata merged into the produced context item.
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
        """Deserialise from a JSON-compatible dict produced by :meth:`to_dict`.

        Raises:
            ConfigError: If required fields are missing or user-supplied enum /
                collection fields are malformed.
        """
        if not isinstance(data, Mapping):
            msg = f"MemoryEntry: expected a mapping, got {type(data).__name__}"
            raise ConfigError(msg)
        try:
            entry_id = str(data["id"])
            text = str(data["text"])
        except KeyError as exc:
            msg = f"MemoryEntry: missing required field {exc.args[0]!r}"
            raise ConfigError(msg) from exc

        sensitivity_raw = data.get("sensitivity", Sensitivity.public.value)
        try:
            sensitivity = Sensitivity(sensitivity_raw)
        except ValueError as exc:
            msg = f"MemoryEntry: invalid sensitivity {sensitivity_raw!r}"
            raise ConfigError(msg) from exc

        tags_raw = data.get("tags", [])
        if not isinstance(tags_raw, list):
            msg = "MemoryEntry: field 'tags' must be a list"
            raise ConfigError(msg)

        metadata_raw = data.get("metadata", {})
        if not isinstance(metadata_raw, Mapping):
            msg = "MemoryEntry: field 'metadata' must be a mapping"
            raise ConfigError(msg)

        return cls(
            id=entry_id,
            text=text,
            source=str(data.get("source", "")),
            timestamp=float(data.get("timestamp", 0.0)),
            scope=str(data.get("scope", "")),
            sensitivity=sensitivity,
            confidence=float(data.get("confidence", 1.0)),
            expires_at=(None if data.get("expires_at") is None else float(data["expires_at"])),
            tags=list(tags_raw),
            metadata=dict(metadata_raw),
        )

    def is_expired(self, *, now: float | None = None) -> bool:
        """Return ``True`` if the entry has passed its ``expires_at`` boundary.

        Args:
            now: UNIX seconds reference time. ``None`` falls back to
                :func:`time.time`.
        """
        if self.expires_at is None:
            return False
        reference = time.time() if now is None else now
        return reference >= self.expires_at


__all__ = ["MemoryEntry", "PHASE_SCOPE_PREFERENCES"]

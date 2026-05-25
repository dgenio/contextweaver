"""Dataclasses and constants for session handoff packs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from contextweaver.types import ArtifactRef

#: Schema version for :class:`SessionHandoffPack` payloads.
HANDOFF_PACK_VERSION = "1"


#: The five canonical handoff buckets. Order is significant for rendering.
HANDOFF_CATEGORIES: tuple[str, ...] = (
    "decision",
    "convention",
    "unresolved",
    "pitfall",
    "next_step",
)


@dataclass
class HandoffEntry:
    """A single classified handoff item.

    Attributes:
        id: Stable identifier, usually the source context item ID.
        text: Entry text after sensitivity filtering and firewall processing.
        category: One of :data:`HANDOFF_CATEGORIES`.
        source_ids: IDs of source event-log items this entry cites.
        confidence: Classification confidence.
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
        next_inspections: Recommended files / artefacts to inspect first.
        artifact_refs: Artefact references cited by included entries.
        sensitivity_dropped: Count of items removed by sensitivity filtering.
        token_estimate: Cumulative token cost of included entry texts.
        version: Schema version tag.
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


def category_attr(category: str) -> str:
    """Map a category name to its :class:`SessionHandoffPack` field name."""
    return {
        "decision": "decisions",
        "convention": "conventions",
        "unresolved": "unresolved_tasks",
        "pitfall": "pitfalls",
        "next_step": "next_inspections",
    }[category]


__all__ = [
    "HANDOFF_CATEGORIES",
    "HANDOFF_PACK_VERSION",
    "HandoffEntry",
    "SessionHandoffPack",
    "category_attr",
]

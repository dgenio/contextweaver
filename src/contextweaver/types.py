"""Core types for contextweaver.

Defines all enums and dataclasses used across both the Context Engine and the
Routing Engine.  Every dataclass implements ``to_dict`` / ``from_dict``
for easy serialisation to JSON-compatible dicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Sensitivity(str, Enum):
    """Data sensitivity level attached to a ContextItem."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class ItemKind(str, Enum):
    """The semantic kind of a ContextItem."""

    USER_TURN = "user_turn"
    AGENT_MSG = "agent_msg"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    DOC_SNIPPET = "doc_snippet"
    MEMORY_FACT = "memory_fact"
    PLAN_STATE = "plan_state"
    POLICY = "policy"


class Phase(str, Enum):
    """Execution phase that determines the active token budget."""

    ROUTE = "route"
    CALL = "call"
    INTERPRET = "interpret"
    ANSWER = "answer"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SelectableItem:
    """A unified representation of a tool, agent, skill, or internal function.

    This is the single vocabulary the Routing Engine operates on.
    """

    id: str
    kind: Literal["tool", "agent", "skill", "internal"]
    name: str
    description: str
    tags: list[str] = field(default_factory=list)
    namespace: str = ""
    args_schema: dict[str, Any] | None = None
    side_effects: bool = False
    cost_hint: Literal["free", "low", "medium", "high"] = "low"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "description": self.description,
            "tags": list(self.tags),
            "namespace": self.namespace,
            "args_schema": dict(self.args_schema) if self.args_schema else None,
            "side_effects": self.side_effects,
            "cost_hint": self.cost_hint,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SelectableItem:
        """Deserialise from a JSON-compatible dict."""
        schema = data.get("args_schema")
        return cls(
            id=data["id"],
            kind=data["kind"],
            name=data["name"],
            description=data["description"],
            tags=list(data.get("tags", [])),
            namespace=data.get("namespace", ""),
            args_schema=dict(schema) if schema else None,
            side_effects=bool(data.get("side_effects", False)),
            cost_hint=data.get("cost_hint", "low"),
            metadata=dict(data.get("metadata", {})),
        )


#: Backward-compatibility alias.
ToolCard = SelectableItem


@dataclass
class ArtifactRef:
    """A lightweight reference to an out-of-band artifact stored in an ArtifactStore."""

    handle: str
    media_type: str
    size_bytes: int | None = None
    label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "handle": self.handle,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArtifactRef:
        """Deserialise from a JSON-compatible dict."""
        sb = data.get("size_bytes")
        return cls(
            handle=data["handle"],
            media_type=data["media_type"],
            size_bytes=int(sb) if sb is not None else None,
            label=data.get("label"),
        )


@dataclass
class ContextItem:
    """A single entry in the event log / context pipeline."""

    id: str
    kind: ItemKind
    text: str
    token_estimate: int
    metadata: dict[str, Any] = field(default_factory=dict)
    parent_id: str | None = None
    artifact_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "id": self.id,
            "kind": self.kind.value,
            "text": self.text,
            "token_estimate": self.token_estimate,
            "metadata": dict(self.metadata),
            "parent_id": self.parent_id,
            "artifact_ref": self.artifact_ref,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextItem:
        """Deserialise from a JSON-compatible dict."""
        return cls(
            id=data["id"],
            kind=ItemKind(data["kind"]),
            text=data["text"],
            token_estimate=int(data.get("token_estimate", 0)),
            metadata=dict(data.get("metadata", {})),
            parent_id=data.get("parent_id"),
            artifact_ref=data.get("artifact_ref"),
        )


@dataclass
class ViewSpec:
    """Specifies a named view (a filtered/projected representation) of an artifact."""

    view_id: str
    label: str
    selector: dict[str, Any] = field(default_factory=dict)
    artifact_ref: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "view_id": self.view_id,
            "label": self.label,
            "selector": dict(self.selector),
            "artifact_ref": self.artifact_ref,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ViewSpec:
        """Deserialise from a JSON-compatible dict."""
        return cls(
            view_id=data["view_id"],
            label=data["label"],
            selector=dict(data.get("selector", {})),
            artifact_ref=data.get("artifact_ref", ""),
        )


@dataclass
class ResultEnvelope:
    """Wraps the output of a tool call with LLM-friendly summaries and structured data."""

    status: Literal["ok", "partial", "error"]
    summary: str
    facts: dict[str, Any] = field(default_factory=dict)
    artifacts: list[ArtifactRef] = field(default_factory=list)
    views: list[ViewSpec] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "status": self.status,
            "summary": self.summary,
            "facts": dict(self.facts),
            "artifacts": [a.to_dict() for a in self.artifacts],
            "views": [v.to_dict() for v in self.views],
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResultEnvelope:
        """Deserialise from a JSON-compatible dict."""
        return cls(
            status=data["status"],
            summary=data["summary"],
            facts=dict(data.get("facts", {})),
            artifacts=[ArtifactRef.from_dict(a) for a in data.get("artifacts", [])],
            views=[ViewSpec.from_dict(v) for v in data.get("views", [])],
            provenance=dict(data.get("provenance", {})),
        )


@dataclass
class BuildStats:
    """Typed statistics for a context build."""

    tokens_per_section: dict[str, int] = field(default_factory=dict)
    total_candidates: int = 0
    included_count: int = 0
    dropped_count: int = 0
    dropped_reasons: dict[str, int] = field(default_factory=dict)
    dedup_removed: int = 0
    dependency_closures: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "tokens_per_section": dict(self.tokens_per_section),
            "total_candidates": self.total_candidates,
            "included_count": self.included_count,
            "dropped_count": self.dropped_count,
            "dropped_reasons": dict(self.dropped_reasons),
            "dedup_removed": self.dedup_removed,
            "dependency_closures": self.dependency_closures,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BuildStats:
        """Deserialise from a JSON-compatible dict."""
        return cls(
            tokens_per_section=dict(data.get("tokens_per_section", {})),
            total_candidates=int(data.get("total_candidates", 0)),
            included_count=int(data.get("included_count", 0)),
            dropped_count=int(data.get("dropped_count", 0)),
            dropped_reasons=dict(data.get("dropped_reasons", {})),
            dedup_removed=int(data.get("dedup_removed", 0)),
            dependency_closures=int(data.get("dependency_closures", 0)),
        )


__all__ = [
    "ArtifactRef",
    "BuildStats",
    "ContextItem",
    "ItemKind",
    "Phase",
    "ResultEnvelope",
    "SelectableItem",
    "Sensitivity",
    "ToolCard",
    "ViewSpec",
]

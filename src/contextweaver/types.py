"""Core types for contextweaver.

Defines all enums and dataclasses used across both the Context Engine and the
Routing Engine.  Every dataclass implements :meth:`to_dict` / :meth:`from_dict`
for easy serialisation to JSON-compatible dicts.

Output / result types (:class:`ResultEnvelope`, :class:`BuildStats`,
:class:`ContextPack`, :class:`ChoiceCard`) live in :mod:`contextweaver.envelope`
and are re-exported here for backward compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Sensitivity(str, Enum):
    """Data sensitivity level attached to a :class:`ContextItem`."""

    public = "public"
    internal = "internal"
    confidential = "confidential"
    restricted = "restricted"


class ItemKind(str, Enum):
    """The semantic kind of a :class:`ContextItem`."""

    user_turn = "user_turn"
    agent_msg = "agent_msg"
    tool_call = "tool_call"
    tool_result = "tool_result"
    doc_snippet = "doc_snippet"
    memory_fact = "memory_fact"
    plan_state = "plan_state"
    policy = "policy"


class Phase(str, Enum):
    """Execution phase that determines the active token budget."""

    route = "route"
    call = "call"
    interpret = "interpret"
    answer = "answer"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SelectableItem:
    """A unified representation of a tool, agent, skill, or internal function.

    This is the single vocabulary the Routing Engine operates on.  Use the
    :data:`ToolCard` alias when you want to emphasise the tool-card framing.
    """

    id: str
    kind: Literal["tool", "agent", "skill", "internal"]
    name: str
    description: str
    tags: list[str] = field(default_factory=list)
    namespace: str = ""
    args_schema: dict[str, Any] = field(default_factory=dict)
    examples: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    side_effects: bool = False
    cost_hint: float = 0.0
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
            "args_schema": dict(self.args_schema),
            "examples": list(self.examples),
            "constraints": dict(self.constraints),
            "side_effects": self.side_effects,
            "cost_hint": self.cost_hint,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SelectableItem:
        """Deserialise from a JSON-compatible dict."""
        return cls(
            id=data["id"],
            kind=data["kind"],
            name=data["name"],
            description=data["description"],
            tags=list(data.get("tags", [])),
            namespace=data.get("namespace", ""),
            args_schema=dict(data.get("args_schema", {})),
            examples=list(data.get("examples", [])),
            constraints=dict(data.get("constraints", {})),
            side_effects=bool(data.get("side_effects", False)),
            cost_hint=float(data.get("cost_hint", 0.0)),
            metadata=dict(data.get("metadata", {})),
        )


#: Alias — use when emphasising the LLM-facing card framing.
ToolCard = SelectableItem


@dataclass
class ArtifactRef:
    """A lightweight reference to an out-of-band artifact stored in an ArtifactStore."""

    handle: str
    media_type: str
    size_bytes: int
    label: str = ""

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
        return cls(
            handle=data["handle"],
            media_type=data["media_type"],
            size_bytes=int(data["size_bytes"]),
            label=data.get("label", ""),
        )


@dataclass
class ContextItem:
    """A single entry in the event log / context pipeline.

    *parent_id* enables the dependency-closure pass that pulls in prerequisite
    items even when they fall outside the budget window.
    """

    id: str
    kind: ItemKind
    text: str
    token_estimate: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    parent_id: str | None = None
    artifact_ref: ArtifactRef | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "id": self.id,
            "kind": self.kind.value,
            "text": self.text,
            "token_estimate": self.token_estimate,
            "metadata": dict(self.metadata),
            "parent_id": self.parent_id,
            "artifact_ref": self.artifact_ref.to_dict() if self.artifact_ref else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextItem:
        """Deserialise from a JSON-compatible dict."""
        artifact_raw = data.get("artifact_ref")
        return cls(
            id=data["id"],
            kind=ItemKind(data["kind"]),
            text=data["text"],
            token_estimate=int(data.get("token_estimate", 0)),
            metadata=dict(data.get("metadata", {})),
            parent_id=data.get("parent_id"),
            artifact_ref=ArtifactRef.from_dict(artifact_raw) if artifact_raw else None,
        )


@dataclass
class ViewSpec:
    """Specifies a named view (a filtered/projected representation) of an artifact."""

    view_id: str
    label: str
    selector: dict[str, Any] = field(default_factory=dict)
    artifact_ref: ArtifactRef | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "view_id": self.view_id,
            "label": self.label,
            "selector": dict(self.selector),
            "artifact_ref": self.artifact_ref.to_dict() if self.artifact_ref else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ViewSpec:
        """Deserialise from a JSON-compatible dict."""
        artifact_raw = data.get("artifact_ref")
        return cls(
            view_id=data["view_id"],
            label=data["label"],
            selector=dict(data.get("selector", {})),
            artifact_ref=ArtifactRef.from_dict(artifact_raw) if artifact_raw else None,
        )


# ---------------------------------------------------------------------------
# Re-exports from envelope.py (backward compatibility)
# ---------------------------------------------------------------------------

from contextweaver.envelope import (  # noqa: E402
    BuildStats,
    ChoiceCard,
    ContextPack,
    ResultEnvelope,
)

__all__ = [
    "ArtifactRef",
    "BuildStats",
    "ChoiceCard",
    "ContextItem",
    "ContextPack",
    "ItemKind",
    "Phase",
    "ResultEnvelope",
    "SelectableItem",
    "Sensitivity",
    "ToolCard",
    "ViewSpec",
]

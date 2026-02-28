"""Core types for contextweaver.

Defines all enums and dataclasses used across both the Context Engine and the
Routing Engine.  Every dataclass implements :meth:`to_dict` / :meth:`from_dict`
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


@dataclass
class ResultEnvelope:
    """Wraps the output of a tool call with LLM-friendly summaries and structured data.

    Raw tool outputs are stored out-of-band in the ArtifactStore; the LLM sees
    only *summary*, *facts*, and *views*.
    """

    status: Literal["ok", "partial", "error"]
    summary: str
    facts: list[str] = field(default_factory=list)
    artifacts: list[ArtifactRef] = field(default_factory=list)
    views: list[ViewSpec] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "status": self.status,
            "summary": self.summary,
            "facts": list(self.facts),
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
            facts=list(data.get("facts", [])),
            artifacts=[ArtifactRef.from_dict(a) for a in data.get("artifacts", [])],
            views=[ViewSpec.from_dict(v) for v in data.get("views", [])],
            provenance=dict(data.get("provenance", {})),
        )


@dataclass
class BuildStats:
    """Diagnostic statistics produced by a context build pass."""

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


@dataclass
class ContextPack:
    """The final output of the Context Engine: a rendered prompt with diagnostics."""

    prompt: str
    stats: BuildStats = field(default_factory=BuildStats)
    phase: Phase = Phase.answer

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "prompt": self.prompt,
            "stats": self.stats.to_dict(),
            "phase": self.phase.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextPack:
        """Deserialise from a JSON-compatible dict."""
        return cls(
            prompt=data["prompt"],
            stats=BuildStats.from_dict(data.get("stats", {})),
            phase=Phase(data.get("phase", Phase.answer.value)),
        )


@dataclass
class ChoiceCard:
    """A compact, LLM-friendly representation of a :class:`SelectableItem`.

    Never includes full arg schemas — keeps prompt token usage minimal.
    """

    id: str
    name: str
    description: str
    tags: list[str] = field(default_factory=list)
    cost_hint: float = 0.0
    side_effects: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tags": list(self.tags),
            "cost_hint": self.cost_hint,
            "side_effects": self.side_effects,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChoiceCard:
        """Deserialise from a JSON-compatible dict."""
        return cls(
            id=data["id"],
            name=data["name"],
            description=data["description"],
            tags=list(data.get("tags", [])),
            cost_hint=float(data.get("cost_hint", 0.0)),
            side_effects=bool(data.get("side_effects", False)),
        )

"""Result and output types for contextweaver.

Contains the "output" dataclasses produced by the Context Engine and the
Routing Engine: :class:`ResultEnvelope`, :class:`BuildStats`,
:class:`ContextPack`, :class:`ChoiceCard`, and :class:`HydrationResult`.

Every dataclass implements :meth:`to_dict` / :meth:`from_dict` for easy
serialisation to JSON-compatible dicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from contextweaver.types import ArtifactRef, Phase, SelectableItem, ViewSpec


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
    header_footer_tokens: int = 0

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
            "header_footer_tokens": self.header_footer_tokens,
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
            header_footer_tokens=int(data.get("header_footer_tokens", 0)),
        )


@dataclass
class ContextPack:
    """The final output of the Context Engine: a rendered prompt with diagnostics.

    *envelopes* carries the :class:`ResultEnvelope` objects produced by the
    context firewall so that callers can access extracted facts, summaries,
    and artifact provenance without re-processing tool results.
    """

    prompt: str
    stats: BuildStats = field(default_factory=BuildStats)
    phase: Phase = Phase.answer
    envelopes: list[ResultEnvelope] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "prompt": self.prompt,
            "stats": self.stats.to_dict(),
            "phase": self.phase.value,
            "envelopes": [e.to_dict() for e in self.envelopes],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextPack:
        """Deserialise from a JSON-compatible dict."""
        return cls(
            prompt=data["prompt"],
            stats=BuildStats.from_dict(data.get("stats", {})),
            phase=Phase(data.get("phase", Phase.answer.value)),
            envelopes=[ResultEnvelope.from_dict(e) for e in data.get("envelopes", [])],
        )


@dataclass
class ChoiceCard:
    """A compact, LLM-friendly representation of a :class:`SelectableItem`.

    Never includes full arg schemas — keeps prompt token usage minimal.
    ``has_schema`` is a boolean flag indicating whether the source item has
    an argument schema; the schema itself is never included.
    """

    id: str
    name: str
    description: str
    tags: list[str] = field(default_factory=list)
    kind: str = "tool"
    namespace: str = ""
    has_schema: bool = False
    score: float | None = None
    cost_hint: float = 0.0
    side_effects: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        d: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tags": list(self.tags),
            "kind": self.kind,
            "namespace": self.namespace,
            "has_schema": self.has_schema,
            "cost_hint": self.cost_hint,
            "side_effects": self.side_effects,
        }
        if self.score is not None:
            d["score"] = self.score
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChoiceCard:
        """Deserialise from a JSON-compatible dict."""
        return cls(
            id=data["id"],
            name=data["name"],
            description=data["description"],
            tags=list(data.get("tags", [])),
            kind=data.get("kind", "tool"),
            namespace=data.get("namespace", ""),
            has_schema=bool(data.get("has_schema", False)),
            score=data.get("score"),
            cost_hint=float(data.get("cost_hint", 0.0)),
            side_effects=bool(data.get("side_effects", False)),
        )


@dataclass
class HydrationResult:
    """Full schema and metadata for a tool selected after routing.

    Returned by :meth:`~contextweaver.routing.catalog.Catalog.hydrate` to
    provide all information needed to build a ``Phase.call`` prompt.
    """

    item: SelectableItem
    args_schema: dict[str, Any]
    examples: list[str]
    constraints: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "item": self.item.to_dict(),
            "args_schema": dict(self.args_schema),
            "examples": list(self.examples),
            "constraints": dict(self.constraints),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HydrationResult:
        """Deserialise from a JSON-compatible dict."""
        return cls(
            item=SelectableItem.from_dict(data["item"]),
            args_schema=dict(data.get("args_schema", {})),
            examples=list(data.get("examples", [])),
            constraints=dict(data.get("constraints", {})),
        )

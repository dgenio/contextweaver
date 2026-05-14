"""Result and output types for contextweaver.

Contains the "output" dataclasses produced by the Context Engine and the
Routing Engine: :class:`ResultEnvelope`, :class:`BuildStats`,
:class:`ContextPack`, :class:`ChoiceCard`, :class:`HydrationResult`, and
:class:`RoutingDecision`.

Every dataclass implements :meth:`to_dict` / :meth:`from_dict` for easy
serialisation to JSON-compatible dicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
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


@dataclass
class RoutingDecision:
    """Structured result of a routing call, shaped for weaver-spec interop.

    Mirrors the field set of the weaver-spec ``RoutingDecision`` contract
    (`weaver-spec <https://github.com/dgenio/weaver-spec>`_) but stores the
    options as a flat list of contextweaver 1:1 :class:`ChoiceCard` instances
    rather than the spec's 1:N menu shape (each spec ``ChoiceCard`` carries an
    ``items`` list of ``SelectableItem``).  Issue #151.

    .. important::
       :meth:`to_dict` produces a contextweaver-shaped JSON payload, **not**
       a spec-compliant document.  For schema-valid output that round-trips
       through ``weaver_contracts``, use
       :func:`contextweaver.adapters.weaver_contracts.to_weaver_routing_decision`
       and serialise its result with the standard ``dataclasses.asdict``
       helper documented in ``docs/weaver_spec_mapping.md``.

    Distinct from :class:`~contextweaver.routing.router.RouteResult`, which is
    the internal beam-search output.  Use
    :meth:`~contextweaver.routing.router.RouteResult.to_routing_decision` to
    build a ``RoutingDecision`` from a routing call.

    Attributes:
        id: Unique identifier for this decision.  Non-empty.
        choice_cards: The bounded choices presented during the routing call.
            Typically the ``RouteResult.candidate_items`` rendered as
            :class:`ChoiceCard` instances.  When mapped via the adapter the
            full list is grouped into a single spec ``ChoiceCard`` menu.
        timestamp: Timezone-aware UTC timestamp of when the decision was
            created.  Serialised as ISO 8601.
        selected_item_id: Optional ID of the item the downstream LLM picked.
            ``None`` while the response is pending.
        selected_card_id: Optional ID of the :class:`ChoiceCard` that
            contained the selected item.
        context_summary: Optional brief summary of the context that drove
            this routing decision.  For debugging and audit.
        metadata: Optional implementation-specific metadata.  Use the
            ``"_contextweaver"`` namespace for CW-specific fields when
            interoperating with the weaver-spec adapter.

    Example:
        >>> from datetime import datetime, timezone
        >>> from contextweaver.envelope import RoutingDecision, ChoiceCard
        >>> card = ChoiceCard(id="t1", name="search", description="Search")
        >>> rd = RoutingDecision(
        ...     id="dec-1",
        ...     choice_cards=[card],
        ...     timestamp=datetime(2026, 5, 14, tzinfo=timezone.utc),
        ... )
        >>> rd.id
        'dec-1'
    """

    id: str
    choice_cards: list[ChoiceCard]
    timestamp: datetime
    selected_item_id: str | None = None
    selected_card_id: str | None = None
    context_summary: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict (ISO 8601 timestamp)."""
        d: dict[str, Any] = {
            "id": self.id,
            "choice_cards": [c.to_dict() for c in self.choice_cards],
            "timestamp": self.timestamp.isoformat(),
            "metadata": dict(self.metadata),
        }
        if self.selected_item_id is not None:
            d["selected_item_id"] = self.selected_item_id
        if self.selected_card_id is not None:
            d["selected_card_id"] = self.selected_card_id
        if self.context_summary is not None:
            d["context_summary"] = self.context_summary
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoutingDecision:
        """Deserialise from a JSON-compatible dict.

        ``timestamp`` may be a ``datetime`` instance or an ISO 8601 string.
        The common RFC 3339 ``Z`` UTC suffix is normalised to ``+00:00`` so
        payloads validated against the spec's ``date-time`` format parse on
        Python 3.10 (the stdlib ``datetime.fromisoformat`` only learned to
        accept ``Z`` in 3.11).  Naive timestamps are assumed to be UTC.
        """
        raw_ts = data.get("timestamp")
        ts: datetime
        if isinstance(raw_ts, datetime):
            ts = raw_ts
        elif isinstance(raw_ts, str):
            normalised = raw_ts[:-1] + "+00:00" if raw_ts.endswith("Z") else raw_ts
            ts = datetime.fromisoformat(normalised)
        else:
            ts = datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return cls(
            id=data["id"],
            choice_cards=[ChoiceCard.from_dict(c) for c in data.get("choice_cards", [])],
            timestamp=ts,
            selected_item_id=data.get("selected_item_id"),
            selected_card_id=data.get("selected_card_id"),
            context_summary=data.get("context_summary"),
            metadata=dict(data.get("metadata", {})),
        )

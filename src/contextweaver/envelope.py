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

from contextweaver.exceptions import ValidationError
from contextweaver.types import ArtifactRef, Phase, SelectableItem, ViewSpec

#: Schema version for :meth:`BuildStats.report_dict` payloads.  Version 2
#: adds per-item drop attribution and gives candidate counts one consistent
#: pre-sensitivity meaning (issues #414 / #459).
BUILD_STATS_REPORT_VERSION: int = 2

#: Canonical firewall strategy labels recorded on :class:`FirewallStats`
#: (issues #402 / #404 / #406).
#:
#: - ``"noop"`` — firewall not applicable (e.g. non-``tool_result`` item).
#: - ``"passthrough"`` — under threshold; caller payload returned shape-unchanged
#:   (issue #403).
#: - ``"summary"`` — deterministic, rule-based text summary (no LLM).
#: - ``"structured"`` — lossless JSON field projection (issue #406; no LLM).
#: - ``"llm_summary"`` — model-assisted summary (issue #384; LLM touched the data).
FIREWALL_STRATEGIES: tuple[str, ...] = (
    "noop",
    "passthrough",
    "summary",
    "structured",
    "llm_summary",
)


@dataclass
class FirewallStats:
    """First-class diagnostics for a single context-firewall decision (issue #402).

    Answers the two questions an integrator cares about that the aggregate
    :class:`BuildStats` counters cannot: **was the firewall triggered at all?**
    and **if so, how much was saved?** — in both characters and tokens.

    The ``strategy`` and ``summarized_by_llm`` fields double as the
    determinism provenance signal (issue #404): a value of ``"structured"`` /
    ``"summary"`` with ``summarized_by_llm=False`` is a model-free,
    byte-deterministic path suitable for citing in a compliance review;
    ``"llm_summary"`` means a model produced the inline representation.

    Attributes:
        triggered: ``True`` when the firewall offloaded the payload out-of-band
            (``strategy`` in ``{"summary", "structured", "llm_summary"}``);
            ``False`` for ``"noop"`` / ``"passthrough"``.
        strategy: One of :data:`FIREWALL_STRATEGIES`.
        threshold_chars: The character threshold the payload was compared
            against.  ``0`` when not applicable.
        original_chars / original_tokens: Size of the raw payload.
        summary_chars / summary_tokens: Size of the inline representation that
            reached the prompt.
        artifact_ref: Handle of the offloaded raw payload, or ``None`` when the
            payload was passed through / not stored.
        summarized_by_llm: ``True`` only when an LLM produced the summary.
    """

    triggered: bool
    strategy: str
    threshold_chars: int = 0
    original_chars: int = 0
    original_tokens: int = 0
    summary_chars: int = 0
    summary_tokens: int = 0
    artifact_ref: str | None = None
    summarized_by_llm: bool = False

    @property
    def chars_saved(self) -> int:
        """Characters kept out of the prompt (``original - summary``, ≥ 0)."""
        return max(self.original_chars - self.summary_chars, 0)

    @property
    def tokens_saved(self) -> int:
        """Tokens kept out of the prompt (``original - summary``, ≥ 0)."""
        return max(self.original_tokens - self.summary_tokens, 0)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "triggered": self.triggered,
            "strategy": self.strategy,
            "threshold_chars": self.threshold_chars,
            "original_chars": self.original_chars,
            "original_tokens": self.original_tokens,
            "summary_chars": self.summary_chars,
            "summary_tokens": self.summary_tokens,
            "artifact_ref": self.artifact_ref,
            "summarized_by_llm": self.summarized_by_llm,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FirewallStats:
        """Deserialise from a JSON-compatible dict."""
        return cls(
            triggered=bool(data.get("triggered", False)),
            strategy=str(data.get("strategy", "noop")),
            threshold_chars=int(data.get("threshold_chars", 0)),
            original_chars=int(data.get("original_chars", 0)),
            original_tokens=int(data.get("original_tokens", 0)),
            summary_chars=int(data.get("summary_chars", 0)),
            summary_tokens=int(data.get("summary_tokens", 0)),
            artifact_ref=data.get("artifact_ref"),
            summarized_by_llm=bool(data.get("summarized_by_llm", False)),
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
    firewall_stats: FirewallStats | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        out: dict[str, Any] = {
            "status": self.status,
            "summary": self.summary,
            "facts": list(self.facts),
            "artifacts": [a.to_dict() for a in self.artifacts],
            "views": [v.to_dict() for v in self.views],
            "provenance": dict(self.provenance),
        }
        if self.firewall_stats is not None:
            out["firewall_stats"] = self.firewall_stats.to_dict()
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResultEnvelope:
        """Deserialise from a JSON-compatible dict."""
        fw_raw = data.get("firewall_stats")
        return cls(
            status=data["status"],
            summary=data["summary"],
            facts=list(data.get("facts", [])),
            artifacts=[ArtifactRef.from_dict(a) for a in data.get("artifacts", [])],
            views=[ViewSpec.from_dict(v) for v in data.get("views", [])],
            provenance=dict(data.get("provenance", {})),
            firewall_stats=FirewallStats.from_dict(fw_raw) if fw_raw else None,
        )


@dataclass
class DroppedItem:
    """Lightweight attribution for one item excluded from a context build.

    Attributes:
        item_id: The excluded :class:`~contextweaver.types.ContextItem` id.
        reason: The recorded exclusion reason. Built-in values commonly
            include ``"sensitivity"``, ``"dedup"``, ``"kind_limit"``,
            and ``"budget"``, but the set is not exhaustive: callers may
            also persist policy- or integration-specific reasons such as
            ``"policy"``.
    """

    item_id: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        """Serialise to a JSON-compatible dict."""
        return {"item_id": self.item_id, "reason": self.reason}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DroppedItem:
        """Deserialise from a JSON-compatible dict."""
        return cls(item_id=str(data["item_id"]), reason=str(data["reason"]))


@dataclass
class BuildStats:
    """Diagnostic statistics produced by a context build pass.

    ``total_candidates`` is the number of phase candidates after dependency
    closure and before sensitivity filtering. ``included_count`` is the final
    number rendered into the prompt. ``dropped_count`` includes every later
    exclusion (sensitivity, deduplication, kind limit, and token budget), so a
    completed build satisfies ``included_count + dropped_count ==
    total_candidates``. ``dropped_items`` carries the matching lightweight
    per-item attribution without requiring ``explain=True``.

    ``token_estimator`` records *which* counter produced the build's token
    numbers (e.g. ``"tiktoken/cl100k_base"``, ``"heuristic/v2"``, or a
    registered provider name), so a budget overshoot can be attributed to an
    estimator path (issue #493). Empty when not stamped by the pipeline.
    """

    tokens_per_section: dict[str, int] = field(default_factory=dict)
    total_candidates: int = 0
    included_count: int = 0
    dropped_count: int = 0
    dropped_reasons: dict[str, int] = field(default_factory=dict)
    dropped_items: list[DroppedItem] = field(default_factory=list)
    dedup_removed: int = 0
    dependency_closures: int = 0
    header_footer_tokens: int = 0
    token_estimator: str = ""
    #: One :class:`FirewallStats` per item the firewall offloaded during the
    #: build (issue #402).  Empty when nothing was firewalled.  Always-on and
    #: cheap — populated from the build's :class:`ResultEnvelope` list.
    firewall_events: list[FirewallStats] = field(default_factory=list)

    @property
    def prompt_tokens(self) -> int:
        """Total tokens in the rendered prompt (sections + header/footer).

        Single source of truth for the "how many tokens did this build emit?"
        question — previously each caller (``extras/otel.py``, ``__main__.py``,
        the OTel hook, scattered example scripts) computed
        ``sum(stats.tokens_per_section.values()) + stats.header_footer_tokens``
        inline.  Issue #106.
        """
        return sum(self.tokens_per_section.values()) + self.header_footer_tokens

    def firewall_summary(self) -> FirewallStats:
        """Aggregate every :attr:`firewall_events` entry into one :class:`FirewallStats`.

        Returns a single roll-up answering "did the firewall fire, and how much
        did it save across this build?" (issue #402):

        - ``triggered`` — ``True`` if any event fired.
        - ``original_*`` / ``summary_*`` — summed across events.
        - ``strategy`` — the common strategy, or ``"mixed"`` when events used
          more than one; ``"noop"`` when there were none.
        - ``summarized_by_llm`` — ``True`` if any event used an LLM.
        - ``artifact_ref`` — the first event's handle (``None`` when none).

        For per-item attribution, read :attr:`firewall_events` directly.
        """
        events = self.firewall_events
        if not events:
            return FirewallStats(triggered=False, strategy="noop")
        strategies = {e.strategy for e in events}
        strategy = next(iter(strategies)) if len(strategies) == 1 else "mixed"
        first_ref = next((e.artifact_ref for e in events if e.artifact_ref), None)
        return FirewallStats(
            triggered=any(e.triggered for e in events),
            strategy=strategy,
            threshold_chars=max((e.threshold_chars for e in events), default=0),
            original_chars=sum(e.original_chars for e in events),
            original_tokens=sum(e.original_tokens for e in events),
            summary_chars=sum(e.summary_chars for e in events),
            summary_tokens=sum(e.summary_tokens for e in events),
            artifact_ref=first_ref,
            summarized_by_llm=any(e.summarized_by_llm for e in events),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "tokens_per_section": dict(self.tokens_per_section),
            "total_candidates": self.total_candidates,
            "included_count": self.included_count,
            "dropped_count": self.dropped_count,
            "dropped_reasons": dict(self.dropped_reasons),
            "dropped_items": [item.to_dict() for item in self.dropped_items],
            "dedup_removed": self.dedup_removed,
            "dependency_closures": self.dependency_closures,
            "header_footer_tokens": self.header_footer_tokens,
            "token_estimator": self.token_estimator,
            "firewall_events": [e.to_dict() for e in self.firewall_events],
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
            dropped_items=[DroppedItem.from_dict(item) for item in data.get("dropped_items", [])],
            dedup_removed=int(data.get("dedup_removed", 0)),
            dependency_closures=int(data.get("dependency_closures", 0)),
            header_footer_tokens=int(data.get("header_footer_tokens", 0)),
            token_estimator=str(data.get("token_estimator", "")),
            firewall_events=[FirewallStats.from_dict(e) for e in data.get("firewall_events", [])],
        )

    def report(
        self,
        format: Literal["text", "rich"] = "text",  # noqa: A002 — public API kwarg
        *,
        phase: str | None = None,
        budget: int | None = None,
    ) -> str:
        """Render a human-readable diagnostic report (issue #106).

        Args:
            format: ``"text"`` for a grep-friendly ASCII report; ``"rich"``
                for a Rich-markup string ready for ``rich.Console.print()``.
            phase: Optional phase label to print at the top of the report.
            budget: Optional token budget; when supplied, the report includes
                ``% Budget`` columns and headroom.

        Returns:
            A deterministic, paste-friendly string.  Same inputs → byte-identical
            output across calls (sorted keys, stable spacing).
        """
        return _render_build_stats_report(self, format=format, phase=phase, budget=budget)

    def report_dict(
        self,
        *,
        phase: str | None = None,
        budget: int | None = None,
    ) -> dict[str, Any]:
        """Return the structured payload backing :meth:`report` (issue #106).

        Intended for programmatic consumers (dashboards, alerts, span attributes
        on :class:`~contextweaver.extras.otel.OTelEventHook`).  Stable schema —
        bumped via the top-level ``version`` field.
        """
        return _build_stats_report_dict(self, phase=phase, budget=budget)


# ---------------------------------------------------------------------------
# BuildStats report rendering (issue #106)
# ---------------------------------------------------------------------------

# Module-private helpers kept at file scope so that the data-layer invariant
# ("no I/O in envelope.py") is preserved: this is pure string formatting.


def _build_stats_report_dict(
    stats: BuildStats,
    *,
    phase: str | None,
    budget: int | None,
) -> dict[str, Any]:
    """Structured payload for :meth:`BuildStats.report_dict`."""
    prompt_tokens = stats.prompt_tokens
    total_tokens = prompt_tokens  # alias preserved for spec clarity
    sections = sorted(stats.tokens_per_section.items())  # deterministic order
    reasons = sorted(stats.dropped_reasons.items())  # deterministic order

    recommendations: list[str] = []
    if budget and budget > 0:
        for name, tokens in sections:
            if tokens / budget > 0.50:
                recommendations.append(
                    f"⚠ {tokens / budget:.0%} of budget used by {name} — "
                    f"consider lowering firewall threshold"
                )
        headroom = budget - total_tokens
        if headroom > 0 and total_tokens / budget < 0.95:
            recommendations.append(f"✓ {headroom / budget:.1%} budget headroom — efficient")
        elif headroom <= 0:
            recommendations.append(
                f"⚠ over budget by {-headroom} tokens — raise the budget or drop more aggressively"
            )

    return {
        "version": BUILD_STATS_REPORT_VERSION,
        "phase": phase,
        "budget": budget,
        "prompt_tokens": prompt_tokens,
        "tokens_per_section": dict(sections),
        "candidates": {
            "total": stats.total_candidates,
            "included": stats.included_count,
            "dropped": stats.dropped_count,
            "deduplicated": stats.dedup_removed,
            "dependency_closures": stats.dependency_closures,
        },
        "dropped_reasons": dict(reasons),
        "dropped_items": [item.to_dict() for item in stats.dropped_items],
        "recommendations": recommendations,
    }


def _render_build_stats_report(
    stats: BuildStats,
    *,
    format: Literal["text", "rich"],  # noqa: A002 — mirrors public BuildStats.report kwarg
    phase: str | None,
    budget: int | None,
) -> str:
    """Render :class:`BuildStats` as ``text`` or ``rich`` markup string.

    Pure string formatting — no I/O.  Determinism is guaranteed by the
    sorted ``tokens_per_section`` / ``dropped_reasons`` iteration order.
    """
    payload = _build_stats_report_dict(stats, phase=phase, budget=budget)

    if format == "rich":
        return _render_rich(payload)
    return _render_text(payload)


def _render_text(payload: dict[str, Any]) -> str:
    """Plain ASCII rendering of the report payload."""
    lines: list[str] = []
    lines.append("=" * 50)
    lines.append("Context Build Report")
    lines.append("=" * 50)
    phase = payload.get("phase")
    budget = payload.get("budget")
    if phase:
        lines.append(f"Phase:  {phase}")
    if budget:
        lines.append(f"Budget: {budget} tokens")
    lines.append("")

    lines.append("-- Candidates --")
    cand = payload["candidates"]
    lines.append(f"  Generated:    {cand['total']}")
    lines.append(f"  Included:     {cand['included']}")
    lines.append(f"  Dropped:      {cand['dropped']}")
    lines.append(f"  Deduplicated: {cand['deduplicated']}")
    lines.append(f"  Dep. closures:{cand['dependency_closures']}")
    lines.append("")

    lines.append("-- Token Usage --")
    sections: dict[str, int] = payload["tokens_per_section"]
    if not sections:
        lines.append("  (no sections rendered)")
    else:
        if budget:
            lines.append(f"  {'Section':<16}{'Tokens':>10}{'% Budget':>12}")
        else:
            lines.append(f"  {'Section':<16}{'Tokens':>10}")
        for name, tokens in sections.items():
            if budget:
                pct = f"{tokens / budget:>7.1%}" if budget else ""
                lines.append(f"  {name:<16}{tokens:>10}{pct:>12}")
            else:
                lines.append(f"  {name:<16}{tokens:>10}")
        lines.append(f"  {'-' * 36}")
        total = payload["prompt_tokens"]
        if budget:
            lines.append(f"  {'Total':<16}{total:>10}{total / budget:>11.1%}")
            remaining = budget - total
            lines.append(f"  {'Remaining':<16}{remaining:>10}{remaining / budget:>11.1%}")
        else:
            lines.append(f"  {'Total':<16}{total:>10}")
    lines.append("")

    reasons: dict[str, int] = payload["dropped_reasons"]
    if reasons:
        lines.append("-- Dropped Items --")
        for reason, count in reasons.items():
            lines.append(f"  {reason}: {count}")
        lines.append("")

    recs: list[str] = payload["recommendations"]
    if recs:
        lines.append("-- Recommendations --")
        for rec in recs:
            lines.append(f"  {rec}")

    return "\n".join(lines)


def _render_rich(payload: dict[str, Any]) -> str:
    """Rich-markup rendering of the report payload.

    Output is a single string with Rich tag spans; callers pipe it through
    ``rich.console.Console.print`` to render colours and panels.  Plain-text
    callers should use ``format="text"`` instead.
    """
    lines: list[str] = []
    phase = payload.get("phase")
    budget = payload.get("budget")
    header_parts = ["[bold cyan]Context Build Report[/bold cyan]"]
    if phase:
        header_parts.append(f"phase=[yellow]{phase}[/yellow]")
    if budget:
        header_parts.append(f"budget=[yellow]{budget}[/yellow] tokens")
    lines.append("  ".join(header_parts))
    lines.append("")

    cand = payload["candidates"]
    lines.append("[bold]Candidates[/bold]")
    lines.append(
        f"  generated={cand['total']}  included=[green]{cand['included']}[/green]  "
        f"dropped=[red]{cand['dropped']}[/red]  "
        f"dedup={cand['deduplicated']}  closures={cand['dependency_closures']}"
    )
    lines.append("")

    sections: dict[str, int] = payload["tokens_per_section"]
    lines.append("[bold]Token Usage[/bold]")
    if not sections:
        lines.append("  [dim](no sections rendered)[/dim]")
    else:
        for name, tokens in sections.items():
            if budget:
                pct = tokens / budget
                colour = "red" if pct > 0.5 else "green"
                lines.append(f"  {name:<16}{tokens:>8}  [{colour}]{pct:>6.1%}[/{colour}]")
            else:
                lines.append(f"  {name:<16}{tokens:>8}")
        total = payload["prompt_tokens"]
        if budget:
            remaining = budget - total
            r_colour = "red" if remaining < 0 else "green"
            lines.append(f"  [bold]{'Total':<16}{total:>8}  {total / budget:>6.1%}[/bold]")
            lines.append(
                f"  [{r_colour}]{'Remaining':<16}{remaining:>8}  "
                f"{remaining / budget:>6.1%}[/{r_colour}]"
            )
        else:
            lines.append(f"  [bold]{'Total':<16}{total:>8}[/bold]")
    lines.append("")

    reasons: dict[str, int] = payload["dropped_reasons"]
    if reasons:
        lines.append("[bold]Dropped Items[/bold]")
        for reason, count in reasons.items():
            lines.append(f"  [red]{reason}[/red]: {count}")
        lines.append("")

    recs: list[str] = payload["recommendations"]
    if recs:
        lines.append("[bold]Recommendations[/bold]")
        for rec in recs:
            lines.append(f"  {rec}")

    return "\n".join(lines)


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


# ChoiceCard size bounds from docs/gateway_spec.md §2.  Centralised here so
# both the dataclass __post_init__ validator and the schema generator stay in
# sync.  Issue #225.
CHOICE_CARD_NAME_MAX_LEN: int = 64
CHOICE_CARD_TAG_MAX_LEN: int = 24
CHOICE_CARD_TAGS_MAX_COUNT: int = 5
CHOICE_CARD_KINDS: tuple[str, ...] = ("tool", "agent", "skill", "internal", "flow")

#: Permitted values for the first-class :attr:`ChoiceCard.safety` field
#: (issue #516).  ``""`` means *unspecified* (the upstream declared no
#: read-only/destructive hint); ``"read_only"`` and ``"destructive"`` are the
#: two MCP-derived safety classes.  The value is a preserved, capping-immune
#: signal — distinct from the droppable ``destructive`` / ``read-only`` *tags*
#: — that runtime policy layers (issue #373) can key on.
CHOICE_CARD_SAFETY_LEVELS: tuple[str, ...] = ("", "read_only", "destructive")


@dataclass
class ChoiceCard:
    """A compact, LLM-friendly representation of a :class:`SelectableItem`.

    Never includes full arg schemas — keeps prompt token usage minimal.
    ``has_schema`` is a boolean flag indicating whether the source item has
    an argument schema; the schema itself is never included.

    Size bounds (see ``docs/gateway_spec.md`` §2 and issue #225):

    - ``name`` ≤ :data:`CHOICE_CARD_NAME_MAX_LEN` (64) characters.
    - ``tags`` ≤ :data:`CHOICE_CARD_TAGS_MAX_COUNT` (5) entries,
      each ≤ :data:`CHOICE_CARD_TAG_MAX_LEN` (24) characters.
    - ``kind`` ∈ :data:`CHOICE_CARD_KINDS`.
    - ``safety`` ∈ :data:`CHOICE_CARD_SAFETY_LEVELS` (issue #516).

    ``safety`` is a first-class, capping-immune mirror of the read-only /
    destructive MCP annotation.  The renderer caps ``tags`` at five entries
    (``gateway_spec.md`` §2.1), so a tag-rich item could otherwise lose its
    ``destructive`` marker; ``safety`` guarantees the signal survives and gives
    runtime policy layers (issue #373) a stable field to key on instead of a
    droppable tag.  It is informational only — like the underlying annotation
    it is **not** an authorization control (see the SECURITY NOTE in
    ``adapters/mcp.py``).

    Violations raise :class:`~contextweaver.exceptions.ValidationError` at
    construction time so the invariants hold for every code path (including
    :meth:`ChoiceCard.from_dict`).  ``ValidationError`` derives from the
    builtin ``ValueError``, so ``except ValueError`` call sites still catch it.
    """

    id: str
    name: str
    description: str
    tags: list[str] = field(default_factory=list)
    kind: Literal["tool", "agent", "skill", "internal", "flow"] = "tool"
    namespace: str = ""
    has_schema: bool = False
    score: float | None = None
    cost_hint: float = 0.0
    side_effects: bool = False
    safety: Literal["", "read_only", "destructive"] = ""

    def __post_init__(self) -> None:
        """Enforce the gateway-spec §2 size bounds (issue #225)."""
        if self.kind not in CHOICE_CARD_KINDS:
            raise ValidationError(
                f"ChoiceCard.kind must be one of {CHOICE_CARD_KINDS}, "
                f"got {self.kind!r}; see docs/gateway_spec.md §2"
            )
        if self.safety not in CHOICE_CARD_SAFETY_LEVELS:
            raise ValidationError(
                f"ChoiceCard.safety must be one of {CHOICE_CARD_SAFETY_LEVELS}, "
                f"got {self.safety!r}; see docs/gateway_spec.md §2.1"
            )
        if len(self.name) > CHOICE_CARD_NAME_MAX_LEN:
            raise ValidationError(
                f"ChoiceCard.name exceeds {CHOICE_CARD_NAME_MAX_LEN} chars "
                f"({len(self.name)}); see docs/gateway_spec.md §2"
            )
        if len(self.tags) > CHOICE_CARD_TAGS_MAX_COUNT:
            raise ValidationError(
                f"ChoiceCard.tags exceeds {CHOICE_CARD_TAGS_MAX_COUNT} entries "
                f"({len(self.tags)}); see docs/gateway_spec.md §2"
            )
        for tag in self.tags:
            if len(tag) > CHOICE_CARD_TAG_MAX_LEN:
                raise ValidationError(
                    f"ChoiceCard.tags entry {tag!r} exceeds "
                    f"{CHOICE_CARD_TAG_MAX_LEN} chars; see docs/gateway_spec.md §2"
                )

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
            "safety": self.safety,
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
            safety=data.get("safety", ""),
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

        ``timestamp`` is required (it is a non-optional field).  A missing or
        unparseable value raises :class:`~contextweaver.exceptions.ValidationError`
        rather than fabricating ``datetime.now()`` — the data layer must
        round-trip losslessly and stay deterministic, so it never invents a
        value a malformed payload did not carry (issue #463).

        Raises:
            ValidationError: If ``timestamp`` is absent or not a ``datetime`` /
                parseable ISO 8601 string.
        """
        raw_ts = data.get("timestamp")
        ts: datetime
        if isinstance(raw_ts, datetime):
            ts = raw_ts
        elif isinstance(raw_ts, str):
            normalised = raw_ts[:-1] + "+00:00" if raw_ts.endswith("Z") else raw_ts
            try:
                ts = datetime.fromisoformat(normalised)
            except ValueError as exc:
                raise ValidationError(
                    f"RoutingDecision.timestamp is not a valid ISO 8601 string: {raw_ts!r}"
                ) from exc
        else:
            raise ValidationError(
                "RoutingDecision.from_dict requires a 'timestamp' (datetime or ISO "
                f"8601 string); got {raw_ts!r}"
            )
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

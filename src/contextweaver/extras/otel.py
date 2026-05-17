"""OpenTelemetry GenAI integration for contextweaver (issue #224).

Provides :class:`OTelEventHook` — an :class:`~contextweaver.protocols.EventHook`
implementation that emits OpenTelemetry spans and metrics conforming to the
official **OpenTelemetry GenAI Semantic Conventions**.  Observability
platforms that already understand the ``gen_ai.*`` namespace (Laminar,
Phoenix, Langfuse, LangSmith) render contextweaver activity as
native agent / tool spans instead of generic ones.

This module requires the ``[otel]`` optional extra::

    pip install 'contextweaver[otel]'

Without that extra, importing this module raises :class:`ImportError` with
the exact install hint above.  The rest of contextweaver works unchanged.

Span shapes
-----------

- ``invoke_agent`` — one span per :meth:`ContextManager.build()`.  Operation
  name ``gen_ai.operation.name = "invoke_agent"``; system identifier
  ``gen_ai.system = "contextweaver"``; ``gen_ai.usage.input_tokens`` is the
  prompt the agent will hand the LLM (i.e. :attr:`BuildStats.prompt_tokens`).
- ``execute_tool`` — one span per :meth:`Router.route()`.  Operation name
  ``gen_ai.operation.name = "execute_tool"``; ``gen_ai.tool.name`` is the
  rank-1 candidate (the routing decision).

Metric shapes
-------------

- ``gen_ai.client.token.usage`` (histogram) — prompt tokens per build,
  with ``gen_ai.operation.name`` and ``gen_ai.token.type`` attributes
  per SemConv.
- ``contextweaver.firewall.interceptions`` (counter) — engine-specific.
- ``contextweaver.items.excluded`` (counter) — engine-specific.
- ``contextweaver.budget.exceeded`` (counter) — engine-specific.
- ``contextweaver.routing.candidates`` (histogram) — engine-specific.

The OTel GenAI semantic conventions are currently under the
``opentelemetry.semconv._incubating`` namespace upstream (the GenAI SemConv
is in **Development** status as of the 1.41 SDK release).  contextweaver
imports them via that path and re-exports the canonical attribute strings.
When upstream graduates them to stable, only the import path changes — the
emitted attribute names are spec-stable.

Privacy guidance
----------------

The default emission does **not** include raw query strings, full tool
descriptions, or any ``args_schema`` content — those can carry sensitive
payloads in some tool catalogs.  When ``otel_emit_experimental=True`` is
passed at construction time, opt-in experimental attributes (e.g. the
``gen_ai.prompt`` raw-prompt attribute) are emitted; only enable that flag
when the observability backend is trusted to handle PII appropriately.
References: https://maketocreate.com/opentelemetry-genai-tracing-ai-agents-without-leaking-pii/.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from contextweaver.envelope import ContextPack
    from contextweaver.types import ContextItem


try:
    from opentelemetry import metrics as _otel_metrics
    from opentelemetry import trace as _otel_trace

    # GenAI SemConv lives under ``_incubating`` while upstream status is
    # "Development".  Pinned via ``opentelemetry-semantic-conventions-ai``
    # or ``opentelemetry-semantic-conventions>=0.48b0`` in pyproject.toml
    # ``[otel]`` extra.  If upstream graduates to stable, only this import
    # path changes — emitted attribute *names* are already spec-stable.
    from opentelemetry.semconv._incubating.attributes import gen_ai_attributes as _ga
    from opentelemetry.semconv._incubating.metrics import gen_ai_metrics as _gm
except ImportError as _otel_import_err:  # pragma: no cover - exercised only when extra is missing
    raise ImportError(
        "OpenTelemetry integration requires the [otel] extra. "
        "Install with: pip install 'contextweaver[otel]'"
    ) from _otel_import_err


#: ``gen_ai.system`` value identifying contextweaver in OTLP-exported telemetry.
#: Hardcoded rather than using ``GenAiSystemValues`` since the upstream enum
#: only lists LLM vendors; the SemConv allows custom values for
#: framework-style emitters.
GEN_AI_SYSTEM_CONTEXTWEAVER: str = "contextweaver"

#: ``gen_ai.operation.name`` value for context-build spans.
GEN_AI_OPERATION_INVOKE_AGENT: str = "invoke_agent"

#: ``gen_ai.operation.name`` value for routing spans.
GEN_AI_OPERATION_EXECUTE_TOOL: str = "execute_tool"


class OTelEventHook:
    """:class:`~contextweaver.protocols.EventHook` emitting OTel GenAI spans + metrics.

    Pass an instance to :class:`~contextweaver.context.manager.ContextManager`
    via ``hook=``; spans are created with shapes matching the GenAI
    Semantic Conventions so Laminar / Phoenix / Langfuse / LangSmith
    render the agent and tool decomposition natively.

    Span names emitted:

    - ``invoke_agent`` — one span per :meth:`ContextManager.build`.
    - ``execute_tool`` — one span per :meth:`Router.route`.
    - ``contextweaver.context.firewall`` — engine-specific, kept for
      detailed audit; not currently part of the GenAI SemConv.
    - ``contextweaver.context.exclude`` — engine-specific.

    Metrics emitted:

    - ``gen_ai.client.token.usage`` (histogram) — prompt tokens.
    - ``contextweaver.firewall.interceptions`` (counter).
    - ``contextweaver.items.excluded`` (counter).
    - ``contextweaver.budget.exceeded`` (counter).
    - ``contextweaver.routing.candidates`` (histogram).

    Args:
        service_name: OTel resource name; defaults to ``"contextweaver"``.
        otel_emit_experimental: When ``True``, emit GenAI SemConv
            attributes that are still flagged experimental upstream
            (e.g. raw-prompt content).  Default ``False`` keeps the
            emission PII-safe.
    """

    def __init__(
        self,
        service_name: str = "contextweaver",
        *,
        otel_emit_experimental: bool = False,
    ) -> None:
        self._tracer = _otel_trace.get_tracer(service_name)
        self._meter = _otel_metrics.get_meter(service_name)
        # GenAI SemConv-named token-usage histogram.
        self._token_hist: Any = self._meter.create_histogram(_gm.GEN_AI_CLIENT_TOKEN_USAGE)
        # Engine-specific counters (no SemConv equivalent yet).
        self._firewall_counter: Any = self._meter.create_counter(
            "contextweaver.firewall.interceptions"
        )
        self._exclude_counter: Any = self._meter.create_counter("contextweaver.items.excluded")
        self._budget_counter: Any = self._meter.create_counter("contextweaver.budget.exceeded")
        self._candidates_hist: Any = self._meter.create_histogram(
            "contextweaver.routing.candidates"
        )
        self._emit_experimental = otel_emit_experimental

    # ------------------------------------------------------------------
    # Context build → invoke_agent span (GenAI SemConv)
    # ------------------------------------------------------------------

    def on_context_built(self, pack: ContextPack) -> None:
        """Emit an ``invoke_agent``-shaped span + token-usage metric."""
        stats = pack.stats
        prompt_tokens = stats.prompt_tokens
        attrs: dict[str, Any] = {
            _ga.GEN_AI_SYSTEM: GEN_AI_SYSTEM_CONTEXTWEAVER,
            _ga.GEN_AI_OPERATION_NAME: GEN_AI_OPERATION_INVOKE_AGENT,
            _ga.GEN_AI_USAGE_INPUT_TOKENS: prompt_tokens,
            # Engine-specific diagnostic attributes — surface under the
            # ``contextweaver.*`` namespace so they don't collide with future
            # SemConv additions.
            "contextweaver.phase": pack.phase.value,
            "contextweaver.candidates.total": stats.total_candidates,
            "contextweaver.candidates.included": stats.included_count,
            "contextweaver.candidates.dropped": stats.dropped_count,
            "contextweaver.candidates.dedup_removed": stats.dedup_removed,
        }
        if self._emit_experimental:
            # Experimental: include the rendered prompt.  PII-prone — only
            # enable when the observability backend is trusted.
            attrs["contextweaver.prompt.rendered"] = pack.prompt
        with self._tracer.start_as_current_span(GEN_AI_OPERATION_INVOKE_AGENT, attributes=attrs):
            pass
        # Histogram per SemConv: tag with operation.name + token.type.
        self._token_hist.record(
            prompt_tokens,
            attributes={
                _ga.GEN_AI_OPERATION_NAME: GEN_AI_OPERATION_INVOKE_AGENT,
                _ga.GEN_AI_TOKEN_TYPE: "input",
                _ga.GEN_AI_SYSTEM: GEN_AI_SYSTEM_CONTEXTWEAVER,
            },
        )

    # ------------------------------------------------------------------
    # Engine-specific events (no canonical SemConv equivalent)
    # ------------------------------------------------------------------

    def on_firewall_triggered(self, item: ContextItem, reason: str) -> None:
        """Record a span + counter increment for one firewall interception."""
        with self._tracer.start_as_current_span(
            "contextweaver.context.firewall",
            attributes={
                "contextweaver.firewall.reason": reason,
                "contextweaver.item.kind": item.kind.value,
            },
        ):
            pass
        self._firewall_counter.add(1, attributes={"contextweaver.firewall.reason": reason})

    def on_items_excluded(self, items: list[ContextItem], reason: str) -> None:
        """Record a span + counter increment for one exclusion batch."""
        with self._tracer.start_as_current_span(
            "contextweaver.context.exclude",
            attributes={
                "contextweaver.exclude.reason": reason,
                "contextweaver.exclude.count": len(items),
            },
        ):
            pass
        self._exclude_counter.add(len(items), attributes={"contextweaver.exclude.reason": reason})

    def on_budget_exceeded(self, requested: int, budget: int) -> None:
        """Increment the budget-exceeded counter with the overrun ratio."""
        self._budget_counter.add(
            1,
            attributes={
                "contextweaver.budget.requested": requested,
                "contextweaver.budget.limit": budget,
            },
        )

    # ------------------------------------------------------------------
    # Route → execute_tool span (GenAI SemConv)
    # ------------------------------------------------------------------

    def on_route_completed(self, tool_ids: list[str]) -> None:
        """Emit an ``execute_tool``-shaped span + candidates-histogram observation.

        The rank-1 candidate (``tool_ids[0]``) populates
        :data:`gen_ai_attributes.GEN_AI_TOOL_NAME` — that's the routing
        decision the LLM is being offered.  The full candidate list is
        surfaced under the ``contextweaver.*`` namespace for callers that
        want to audit the runners-up.
        """
        attrs: dict[str, Any] = {
            _ga.GEN_AI_SYSTEM: GEN_AI_SYSTEM_CONTEXTWEAVER,
            _ga.GEN_AI_OPERATION_NAME: GEN_AI_OPERATION_EXECUTE_TOOL,
            "contextweaver.routing.candidate_count": len(tool_ids),
        }
        if tool_ids:
            attrs[_ga.GEN_AI_TOOL_NAME] = tool_ids[0]
            attrs["contextweaver.routing.candidate_ids"] = tuple(tool_ids)
        with self._tracer.start_as_current_span(GEN_AI_OPERATION_EXECUTE_TOOL, attributes=attrs):
            pass
        self._candidates_hist.record(
            len(tool_ids),
            attributes={
                _ga.GEN_AI_OPERATION_NAME: GEN_AI_OPERATION_EXECUTE_TOOL,
                _ga.GEN_AI_SYSTEM: GEN_AI_SYSTEM_CONTEXTWEAVER,
            },
        )

"""OpenTelemetry integration for contextweaver.

Provides :class:`OTelEventHook` — an :class:`~contextweaver.protocols.EventHook`
implementation that emits OpenTelemetry spans and metrics for every context
build, firewall trigger, item exclusion, budget overrun, and routing call.

This module requires the ``[otel]`` optional extra::

    pip install 'contextweaver[otel]'

Without that extra, importing this module raises :class:`ImportError` with
the exact install hint above. The rest of contextweaver works unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from contextweaver.envelope import ContextPack
    from contextweaver.types import ContextItem


try:
    from opentelemetry import metrics as _otel_metrics
    from opentelemetry import trace as _otel_trace
except ImportError as _otel_import_err:  # pragma: no cover - exercised only when extra is missing
    raise ImportError(
        "OpenTelemetry integration requires the [otel] extra. "
        "Install with: pip install 'contextweaver[otel]'"
    ) from _otel_import_err


class OTelEventHook:
    """:class:`~contextweaver.protocols.EventHook` that emits OTel spans + metrics.

    Pass an instance to :class:`~contextweaver.context.manager.ContextManager`
    via ``hook=``; spans are created for build / firewall / route events and
    metrics are exported to whatever OTel backend you have configured
    (Jaeger, Honeycomb, Datadog, console exporter, …).

    Span names emitted:

    - ``contextweaver.context.build`` — one span per :meth:`ContextManager.build`
    - ``contextweaver.context.firewall`` — one span per firewall interception
    - ``contextweaver.context.exclude`` — one span per item-exclusion event
    - ``contextweaver.routing.route`` — one span per routing call

    Metrics emitted:

    - ``contextweaver.tokens.used`` (histogram) — prompt tokens per build
    - ``contextweaver.firewall.interceptions`` (counter) — total intercepts
    - ``contextweaver.items.excluded`` (counter) — total items dropped
    - ``contextweaver.budget.exceeded`` (counter) — over-budget events
    - ``contextweaver.routing.candidates`` (histogram) — candidates returned
    """

    def __init__(self, service_name: str = "contextweaver") -> None:
        """Initialise tracer + meter for the given service name.

        ``contextweaver.tokens.used`` is recorded as a histogram so it stays
        portable across all ``opentelemetry-api>=1.20`` releases — the
        synchronous ``create_gauge`` instrument only landed in 1.27, and an
        observable gauge would force callers to keep a global "latest
        value" cache. Histograms record per-build distributions which is
        usually the more useful surface for dashboards anyway.
        """
        self._tracer = _otel_trace.get_tracer(service_name)
        self._meter = _otel_metrics.get_meter(service_name)
        self._tokens_hist: Any = self._meter.create_histogram("contextweaver.tokens.used")
        self._firewall_counter: Any = self._meter.create_counter(
            "contextweaver.firewall.interceptions"
        )
        self._exclude_counter: Any = self._meter.create_counter("contextweaver.items.excluded")
        self._budget_counter: Any = self._meter.create_counter("contextweaver.budget.exceeded")
        self._candidates_hist: Any = self._meter.create_histogram(
            "contextweaver.routing.candidates"
        )

    def on_context_built(self, pack: ContextPack) -> None:
        """Record a span + tokens-used histogram observation for one build."""
        stats = pack.stats
        prompt_tokens = sum(stats.tokens_per_section.values()) + stats.header_footer_tokens
        attrs: dict[str, Any] = {
            "phase": pack.phase.value,
            "tokens": prompt_tokens,
            "candidates": stats.total_candidates,
            "included": stats.included_count,
            "dropped": stats.dropped_count,
            "dedup_removed": stats.dedup_removed,
        }
        with self._tracer.start_as_current_span("contextweaver.context.build", attributes=attrs):
            pass
        self._tokens_hist.record(prompt_tokens, attributes={"phase": pack.phase.value})

    def on_firewall_triggered(self, item: ContextItem, reason: str) -> None:
        """Record a span + counter increment for one firewall interception."""
        with self._tracer.start_as_current_span(
            "contextweaver.context.firewall",
            attributes={"reason": reason, "item_kind": item.kind.value},
        ):
            pass
        self._firewall_counter.add(1, attributes={"reason": reason})

    def on_items_excluded(self, items: list[ContextItem], reason: str) -> None:
        """Record a span + counter increment for one exclusion batch."""
        with self._tracer.start_as_current_span(
            "contextweaver.context.exclude",
            attributes={"reason": reason, "count": len(items)},
        ):
            pass
        self._exclude_counter.add(len(items), attributes={"reason": reason})

    def on_budget_exceeded(self, requested: int, budget: int) -> None:
        """Increment the budget-exceeded counter with the overrun ratio."""
        self._budget_counter.add(
            1,
            attributes={"requested": requested, "budget": budget},
        )

    def on_route_completed(self, tool_ids: list[str]) -> None:
        """Record a span + candidates-histogram observation for one route."""
        with self._tracer.start_as_current_span(
            "contextweaver.routing.route",
            attributes={"candidate_count": len(tool_ids)},
        ):
            pass
        self._candidates_hist.record(len(tool_ids))

"""Production observability primitives for contextweaver.

This module provides a stdlib-only metrics layer that aggregates statistics
across many context builds and routing calls — enough to answer questions
like "how many tokens am I saving per build?", "what's the top-k hit rate?",
or "how often does the firewall trigger?".

Two pieces:

- :class:`MetricsCollector` — thread-safe accumulator with a JSON-serialisable
  :meth:`MetricsCollector.summary` snapshot and a :meth:`MetricsCollector.reset`.
- :class:`MetricsHook` — concrete :class:`~contextweaver.protocols.EventHook`
  implementation that wires its callbacks into a :class:`MetricsCollector`.
  Drop-in replacement for :class:`~contextweaver.protocols.NoOpHook`.

For full routing-level metrics (confidence gap, candidate count) wire a
:class:`MetricsCollector` directly into :class:`~contextweaver.context.manager.ContextManager`
via the ``metrics=`` parameter — that path receives the full
:class:`~contextweaver.routing.router.RouteResult`.

For OpenTelemetry export, install the ``[otel]`` extra and use
:class:`~contextweaver.extras.otel.OTelEventHook` instead of (or alongside)
:class:`MetricsHook`.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from contextweaver.envelope import ContextPack
    from contextweaver.routing.router import RouteResult
    from contextweaver.types import ContextItem


logger = logging.getLogger("contextweaver.metrics")


@dataclass
class _Counters:
    """Internal mutable counter state for :class:`MetricsCollector`.

    Per-route statistics are stored as running sums rather than per-call
    lists so memory stays O(1) in long-running processes. Averages are
    derived from ``<sum> / total_routes`` at :meth:`summary` time; running
    maxima are tracked separately for inspection.
    """

    total_builds: int = 0
    total_routes: int = 0
    total_prompt_tokens: int = 0
    total_dropped: int = 0
    total_dedup_removed: int = 0
    firewall_interceptions: int = 0
    items_excluded: int = 0
    budget_exceeded: int = 0
    drop_reasons: dict[str, int] = field(default_factory=dict)
    # Running sums + maxima for the route stream — O(1) memory.
    route_candidate_count_sum: int = 0
    route_candidate_count_max: int = 0
    route_top_score_sum: float = 0.0
    route_top_score_max: float = 0.0
    route_confidence_gap_sum: float = 0.0
    route_confidence_gap_max: float = 0.0


class MetricsCollector:
    """Thread-safe accumulator for context-build and routing metrics.

    Every counter is updated under a single :class:`threading.Lock` so the
    collector can be shared across worker threads (e.g. an async pipeline
    with a thread-pool executor).

    Use :meth:`record_build` for context-build outcomes (typically driven by
    :meth:`MetricsHook.on_context_built`) and :meth:`record_route` for
    routing outcomes (driven from :class:`ContextManager` to capture the
    full :class:`~contextweaver.routing.router.RouteResult`).

    Call :meth:`summary` for a JSON-serialisable snapshot and :meth:`reset`
    to zero every counter.
    """

    def __init__(self, *, log_each_build: bool = False) -> None:
        """Initialise an empty collector.

        Args:
            log_each_build: When ``True``, emit a single INFO log line on
                every recorded build/route. Off by default to keep busy
                pipelines quiet.
        """
        self._lock = threading.Lock()
        self._c = _Counters()
        self._log_each = log_each_build

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_build(self, pack: ContextPack) -> None:
        """Aggregate stats from one context build.

        Args:
            pack: The :class:`~contextweaver.envelope.ContextPack` produced
                by :meth:`~contextweaver.context.manager.ContextManager.build`.
        """
        stats = pack.stats
        prompt_tokens = sum(stats.tokens_per_section.values()) + stats.header_footer_tokens
        with self._lock:
            self._c.total_builds += 1
            self._c.total_prompt_tokens += prompt_tokens
            self._c.total_dropped += stats.dropped_count
            self._c.total_dedup_removed += stats.dedup_removed
            for reason, n in stats.dropped_reasons.items():
                self._c.drop_reasons[reason] = self._c.drop_reasons.get(reason, 0) + n
        if self._log_each:
            logger.info(
                "build: phase=%s tokens=%d candidates=%d included=%d dropped=%d",
                pack.phase.value,
                prompt_tokens,
                stats.total_candidates,
                stats.included_count,
                stats.dropped_count,
            )

    def record_route(self, result: RouteResult) -> None:
        """Aggregate stats from one route call.

        Captures candidate count, top score, and the confidence gap between
        rank-1 and rank-2 (zero when fewer than two candidates). Stored as
        running sums + maxima so memory stays O(1) regardless of how many
        routes are recorded.
        """
        candidate_count = len(result.candidate_ids)
        top_score = result.scores[0] if result.scores else 0.0
        gap = (result.scores[0] - result.scores[1]) if len(result.scores) >= 2 else 0.0
        with self._lock:
            self._c.total_routes += 1
            self._c.route_candidate_count_sum += candidate_count
            self._c.route_top_score_sum += top_score
            self._c.route_confidence_gap_sum += gap
            if candidate_count > self._c.route_candidate_count_max:
                self._c.route_candidate_count_max = candidate_count
            if top_score > self._c.route_top_score_max:
                self._c.route_top_score_max = top_score
            if gap > self._c.route_confidence_gap_max:
                self._c.route_confidence_gap_max = gap
        if self._log_each:
            logger.info(
                "route: candidates=%d top_score=%.4f gap=%.4f",
                candidate_count,
                top_score,
                gap,
            )

    def record_firewall(self) -> None:
        """Increment the firewall-interception counter."""
        with self._lock:
            self._c.firewall_interceptions += 1

    def record_items_excluded(self, n: int) -> None:
        """Increment the items-excluded counter by *n*."""
        with self._lock:
            self._c.items_excluded += n

    def record_budget_exceeded(self) -> None:
        """Increment the budget-exceeded counter."""
        with self._lock:
            self._c.budget_exceeded += 1

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of every counter.

        Output keys are deterministic (alphabetical at the top level) so
        diffs are stable across builds.
        """
        with self._lock:
            c = self._c
            avg_prompt_tokens = c.total_prompt_tokens / c.total_builds if c.total_builds else 0.0
            avg_candidates = c.route_candidate_count_sum / c.total_routes if c.total_routes else 0.0
            avg_top_score = c.route_top_score_sum / c.total_routes if c.total_routes else 0.0
            avg_confidence_gap = (
                c.route_confidence_gap_sum / c.total_routes if c.total_routes else 0.0
            )
            return {
                "avg_candidates_per_route": round(avg_candidates, 4),
                "avg_confidence_gap": round(avg_confidence_gap, 4),
                "avg_prompt_tokens": round(avg_prompt_tokens, 4),
                "avg_top_score": round(avg_top_score, 4),
                "budget_exceeded": c.budget_exceeded,
                "drop_reasons": dict(sorted(c.drop_reasons.items())),
                "firewall_interceptions": c.firewall_interceptions,
                "items_excluded": c.items_excluded,
                "max_candidates_per_route": c.route_candidate_count_max,
                "max_confidence_gap": round(c.route_confidence_gap_max, 4),
                "max_top_score": round(c.route_top_score_max, 4),
                "total_builds": c.total_builds,
                "total_dedup_removed": c.total_dedup_removed,
                "total_dropped": c.total_dropped,
                "total_prompt_tokens": c.total_prompt_tokens,
                "total_routes": c.total_routes,
            }

    def reset(self) -> None:
        """Zero every counter atomically."""
        with self._lock:
            self._c = _Counters()


class MetricsHook:
    """Concrete :class:`~contextweaver.protocols.EventHook` that feeds a collector.

    Pass this to :class:`~contextweaver.context.manager.ContextManager` as the
    ``hook=`` argument; build-level metrics flow through the existing
    :class:`~contextweaver.protocols.EventHook` callbacks.

    For full routing metrics (RouteResult-level), pair this with the same
    :class:`MetricsCollector` passed via ``metrics=`` to ``ContextManager``.
    """

    def __init__(self, collector: MetricsCollector | None = None) -> None:
        """Construct the hook.

        Args:
            collector: External :class:`MetricsCollector` to share with
                ``ContextManager``. When ``None``, a fresh collector is
                created and exposed as :attr:`collector`.
        """
        self.collector = collector if collector is not None else MetricsCollector()

    def on_context_built(self, pack: ContextPack) -> None:
        """Forward to :meth:`MetricsCollector.record_build`."""
        self.collector.record_build(pack)

    def on_firewall_triggered(self, item: ContextItem, reason: str) -> None:
        """Forward to :meth:`MetricsCollector.record_firewall`."""
        _ = item, reason
        self.collector.record_firewall()

    def on_items_excluded(self, items: list[ContextItem], reason: str) -> None:
        """Forward to :meth:`MetricsCollector.record_items_excluded`."""
        _ = reason
        self.collector.record_items_excluded(len(items))

    def on_budget_exceeded(self, requested: int, budget: int) -> None:
        """Forward to :meth:`MetricsCollector.record_budget_exceeded`."""
        _ = requested, budget
        self.collector.record_budget_exceeded()

    def on_route_completed(self, tool_ids: list[str]) -> None:
        """No-op at hook level; route metrics arrive via ``ContextManager.metrics``.

        We deliberately ignore the ``tool_ids``-only signature here: the
        full :class:`~contextweaver.routing.router.RouteResult` carries
        scores and confidence gaps that this collector wants. Wire those
        via :class:`ContextManager`'s ``metrics=`` parameter.
        """
        _ = tool_ids

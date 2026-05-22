"""High-level Context Engine manager for contextweaver.

:class:`ContextManager` orchestrates the full context compilation pipeline:

1. :func:`~contextweaver.context.candidates.generate_candidates` — phase filter
2. :func:`~contextweaver.context.candidates.resolve_dependency_closure` — parent chain expansion
3. :func:`~contextweaver.context.sensitivity.apply_sensitivity_filter` — sensitivity enforcement
4. :func:`~contextweaver.context.firewall.apply_firewall_to_batch` — raw output interception
5. :func:`~contextweaver.context.scoring.score_candidates` — relevance scoring
6. :func:`~contextweaver.context.dedup.deduplicate_candidates` — near-duplicate removal
7. :func:`~contextweaver.context.selection.select_and_pack` — budget-aware selection
8. :func:`~contextweaver.context.prompt.render_context` — prompt assembly
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal, overload

from contextweaver.config import ContextBudget, ContextPolicy, ScoringConfig
from contextweaver.context import ingest as _ingest
from contextweaver.context.candidates import generate_candidates, resolve_dependency_closure
from contextweaver.context.dedup import deduplicate_candidates
from contextweaver.context.explanation import (
    ContextBuildExplanation,
)
from contextweaver.context.explanation import (
    build_explanation as _build_explanation,
)
from contextweaver.context.firewall import apply_firewall_to_batch
from contextweaver.context.prompt import render_context
from contextweaver.context.scoring import score_candidates
from contextweaver.context.selection import select_and_pack
from contextweaver.context.sensitivity import apply_sensitivity_filter
from contextweaver.context.views import ViewRegistry
from contextweaver.envelope import ContextPack, ResultEnvelope
from contextweaver.metrics import MetricsCollector
from contextweaver.profiles import Mode, ProfileConfig
from contextweaver.protocols import (
    ArtifactStore,
    CharDivFourEstimator,
    EpisodicStore,
    EventHook,
    EventLog,
    Extractor,
    FactStore,
    NoOpHook,
    Summarizer,
    TokenEstimator,
)
from contextweaver.store import StoreBundle
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.store.episodic import Episode, InMemoryEpisodicStore
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.store.facts import Fact, InMemoryFactStore
from contextweaver.types import ContextItem, ItemKind, Phase

# Maximum facts injected into the prompt header to prevent unbounded growth.
_MAX_FACT_LINES: int = 64
_MAX_FACT_CHARS: int = 2000

if TYPE_CHECKING:
    from contextweaver.envelope import ChoiceCard
    from contextweaver.routing.catalog import Catalog
    from contextweaver.routing.history import RouteHistory
    from contextweaver.routing.router import Router, RouteResult

logger = logging.getLogger("contextweaver.context")


class ContextManager:
    """Orchestrates the full context compilation pipeline.

    Args:
        event_log: The event log to compile context from.
        artifact_store: Where raw tool outputs are stored out-of-band.
        budget: Per-phase token budget configuration.
        policy: Context policy (allowed kinds, per-kind limits, etc.).
        scoring_config: Weights for the relevance scorer.
        estimator: Token estimator for items without ``token_estimate``.
        hook: Lifecycle event hook.
        stores: Optional :class:`StoreBundle` — fills ``None`` fields with
            in-memory defaults.  If *event_log* or *artifact_store* are also
            provided they take precedence.
        summarizer: Optional :class:`~contextweaver.protocols.Summarizer`
            used by the context firewall.  Defaults to the built-in
            first-paragraph truncation heuristic.
        extractor: Optional :class:`~contextweaver.protocols.Extractor`
            used by the context firewall.  Defaults to the built-in
            :func:`~contextweaver.summarize.extract.extract_facts`.
        metrics: Keyword-only.  Optional
            :class:`~contextweaver.metrics.MetricsCollector`.  When
            supplied, full :class:`~contextweaver.routing.router.RouteResult`
            metrics (candidate count, top score, confidence gap) are
            recorded via :meth:`MetricsCollector.record_route` after
            every routing call orchestrated through this manager.
        profile: Keyword-only.  Optional
            :class:`~contextweaver.profiles.ProfileConfig`.  When
            provided, fills ``budget``, ``policy``, and
            ``scoring_config`` from the profile (per-arg overrides win).
            The profile's :attr:`~contextweaver.profiles.ProfileConfig.routing`
            field is *not* consumed here — pass it to the
            :class:`~contextweaver.routing.router.Router` and
            :class:`~contextweaver.routing.tree.TreeBuilder` directly via
            their ``routing_config`` parameters.
    """

    def __init__(
        self,
        event_log: EventLog | None = None,
        artifact_store: ArtifactStore | None = None,
        budget: ContextBudget | None = None,
        policy: ContextPolicy | None = None,
        scoring_config: ScoringConfig | None = None,
        estimator: TokenEstimator | None = None,
        hook: EventHook | None = None,
        stores: StoreBundle | None = None,
        summarizer: Summarizer | None = None,
        extractor: Extractor | None = None,
        *,
        metrics: MetricsCollector | None = None,
        profile: ProfileConfig | None = None,
    ) -> None:
        _stores = stores or StoreBundle()
        self._event_log: EventLog = event_log or _stores.event_log or InMemoryEventLog()
        self._artifact_store: ArtifactStore = (
            artifact_store or _stores.artifact_store or InMemoryArtifactStore()
        )
        self._episodic_store: EpisodicStore = _stores.episodic_store or InMemoryEpisodicStore()
        self._fact_store: FactStore = _stores.fact_store or InMemoryFactStore()
        # Profile fills any unset config; per-arg overrides win.
        if profile is not None:
            budget = budget if budget is not None else profile.budget
            policy = policy if policy is not None else profile.policy
            scoring_config = scoring_config if scoring_config is not None else profile.scoring
        self._budget = budget or ContextBudget()
        self._policy = policy or ContextPolicy()
        self._scoring = scoring_config or ScoringConfig()
        self._estimator: TokenEstimator = estimator or CharDivFourEstimator()
        self._hook: EventHook = hook or NoOpHook()
        self._view_registry: ViewRegistry = ViewRegistry()
        self._summarizer: Summarizer | None = summarizer
        self._extractor: Extractor | None = extractor
        self._metrics: MetricsCollector | None = metrics
        self._profile: ProfileConfig | None = profile
        self._mode: Mode = profile.mode if profile is not None else Mode.strict

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def event_log(self) -> EventLog:
        """The underlying event log."""
        return self._event_log

    @property
    def artifact_store(self) -> ArtifactStore:
        """The underlying artifact store."""
        return self._artifact_store

    @property
    def episodic_store(self) -> EpisodicStore:
        """The underlying episodic store."""
        return self._episodic_store

    @property
    def fact_store(self) -> FactStore:
        """The underlying fact store."""
        return self._fact_store

    @property
    def view_registry(self) -> ViewRegistry:
        """The view registry for auto-generating drilldown views."""
        return self._view_registry

    @property
    def metrics(self) -> MetricsCollector | None:
        """The optional :class:`~contextweaver.metrics.MetricsCollector`.

        ``None`` unless ``metrics=`` was passed to :meth:`__init__`. When
        present, route-level metrics are recorded automatically via
        :meth:`MetricsCollector.record_route` after every routing call.
        """
        return self._metrics

    @property
    def profile(self) -> ProfileConfig | None:
        """The :class:`ProfileConfig` passed at construction, if any."""
        return self._profile

    @property
    def mode(self) -> Mode:
        """Active determinism :class:`Mode` (default :attr:`Mode.strict`)."""
        return self._mode

    # ------------------------------------------------------------------
    # Ingestion helpers
    # ------------------------------------------------------------------

    def ingest(self, item: ContextItem) -> None:
        """Append *item* to the event log.

        Args:
            item: The context item to ingest.
        """
        _ingest.ingest_item(self._event_log, item)

    def ingest_sync(self, item: ContextItem) -> None:
        """Synchronous alias for :meth:`ingest`."""
        self.ingest(item)

    async def ingest_async(self, item: ContextItem) -> None:
        """Async alias for :meth:`ingest`."""
        self.ingest(item)

    def ingest_tool_result(
        self,
        tool_call_id: str,
        raw_output: str,
        tool_name: str = "",
        media_type: str = "text/plain",
        firewall_threshold: int = 2000,
    ) -> tuple[ContextItem, ResultEnvelope]:
        """Ingest a raw tool result through the context firewall.

        If the raw output exceeds *firewall_threshold* characters it is stored
        in the artifact store and the LLM sees only a summary.  Small outputs
        are also stored in the artifact store (with ``artifact_ref`` set on the
        returned item) to enable drilldown on all tool results regardless of
        size.

        Args:
            tool_call_id: ID of the originating tool call.
            raw_output: Raw tool output string.
            tool_name: Human-readable tool name.
            media_type: MIME type of the output.
            firewall_threshold: Character threshold above which the firewall
                stores the raw output out-of-band.

        Returns:
            A ``(ContextItem, ResultEnvelope)`` tuple.  The item always has a
            non-``None`` ``artifact_ref``.
        """
        return _ingest.ingest_tool_result(
            event_log=self._event_log,
            artifact_store=self._artifact_store,
            hook=self._hook,
            view_registry=self._view_registry,
            summarizer=self._summarizer,
            extractor=self._extractor,
            estimator=self._estimator,
            tool_call_id=tool_call_id,
            raw_output=raw_output,
            tool_name=tool_name,
            media_type=media_type,
            firewall_threshold=firewall_threshold,
        )

    def ingest_tool_result_sync(
        self,
        tool_call_id: str,
        raw_output: str,
        tool_name: str = "",
        media_type: str = "text/plain",
        firewall_threshold: int = 2000,
    ) -> tuple[ContextItem, ResultEnvelope]:
        """Synchronous alias for :meth:`ingest_tool_result`."""
        return self.ingest_tool_result(
            tool_call_id, raw_output, tool_name, media_type, firewall_threshold
        )

    def ingest_mcp_result(
        self,
        tool_call_id: str,
        mcp_result: dict[str, Any],
        tool_name: str,
        firewall_threshold: int = 2000,
    ) -> tuple[ContextItem, ResultEnvelope]:
        """Ingest an MCP tool result with full artifact persistence.

        This is the recommended happy-path API for MCP integration.  It:

        1. Parses the MCP result via :func:`mcp_result_to_envelope`.
        2. Stores binary artifacts (images, resources) in the artifact store.
        3. Applies the context firewall for large text outputs.
        4. Appends the resulting :class:`ContextItem` to the event log.

        Args:
            tool_call_id: ID of the originating tool call.
            mcp_result: Raw MCP tool result dict (with ``content`` list).
            tool_name: Human-readable tool name.
            firewall_threshold: Character threshold above which text output
                is stored out-of-band via the firewall.

        Returns:
            A ``(ContextItem, ResultEnvelope)`` tuple with all artifacts
            persisted in the artifact store.
        """
        return _ingest.ingest_mcp_result(
            event_log=self._event_log,
            artifact_store=self._artifact_store,
            hook=self._hook,
            summarizer=self._summarizer,
            extractor=self._extractor,
            estimator=self._estimator,
            tool_call_id=tool_call_id,
            mcp_result=mcp_result,
            tool_name=tool_name,
            firewall_threshold=firewall_threshold,
        )

    def ingest_mcp_result_sync(
        self,
        tool_call_id: str,
        mcp_result: dict[str, Any],
        tool_name: str,
        firewall_threshold: int = 2000,
    ) -> tuple[ContextItem, ResultEnvelope]:
        """Synchronous alias for :meth:`ingest_mcp_result`."""
        return self.ingest_mcp_result(tool_call_id, mcp_result, tool_name, firewall_threshold)

    def add_fact(self, key: str, value: str, metadata: dict[str, Any] | None = None) -> None:
        """Store a fact in the fact store.

        Args:
            key: Fact key.
            value: Fact value.
            metadata: Optional metadata dict.
        """
        fact_id = f"fact:{key}:{len(self._fact_store.all())}"
        self._fact_store.put(
            Fact(
                fact_id=fact_id,
                key=key,
                value=value,
                metadata=metadata or {},
            )
        )

    def add_fact_sync(self, key: str, value: str, metadata: dict[str, Any] | None = None) -> None:
        """Synchronous alias for :meth:`add_fact`."""
        self.add_fact(key, value, metadata)

    def add_episode(
        self,
        episode_id: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Store an episodic memory summary.

        Args:
            episode_id: Unique episode identifier.
            summary: Summary text.
            metadata: Optional metadata dict.
        """
        self._episodic_store.add(
            Episode(
                episode_id=episode_id,
                summary=summary,
                metadata=metadata or {},
            )
        )

    def add_episode_sync(
        self,
        episode_id: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Synchronous alias for :meth:`add_episode`."""
        self.add_episode(episode_id, summary, metadata)

    # ------------------------------------------------------------------
    # Drilldown
    # ------------------------------------------------------------------

    def drilldown(
        self,
        handle: str,
        selector: dict[str, Any],
        *,
        inject: bool = False,
        parent_id: str | None = None,
    ) -> str:
        """Fetch a slice of a stored artifact via the drilldown protocol.

        Wraps :meth:`~contextweaver.protocols.ArtifactStore.drilldown` and
        optionally injects the result as a new :class:`ContextItem` in the
        event log for subsequent context builds.

        Args:
            handle: Artifact handle to drill into.
            selector: Drilldown selector dict (see
                :meth:`~contextweaver.store.artifacts.InMemoryArtifactStore.drilldown`).
            inject: If ``True``, append the drilldown result as a
                ``tool_result`` :class:`ContextItem` to the event log.
            parent_id: Optional parent item ID for dependency closure when
                *inject* is ``True``.

        Returns:
            The drilldown result text.

        Raises:
            ArtifactNotFoundError: If *handle* is not in the store.
            ContextWeaverError: If the selector type is unknown.
        """
        result = self._artifact_store.drilldown(handle, selector)

        if inject:
            sel_type = selector.get("type", "unknown")
            item_id = f"drilldown:{handle}:{sel_type}:{self._event_log.count()}"
            item = ContextItem(
                id=item_id,
                kind=ItemKind.tool_result,
                text=result,
                token_estimate=self._estimator.estimate(result),
                metadata={"drilldown_handle": handle, "selector": selector},
                parent_id=parent_id,
            )
            self._event_log.append(item)

        return result

    def drilldown_sync(
        self,
        handle: str,
        selector: dict[str, Any],
        *,
        inject: bool = False,
        parent_id: str | None = None,
    ) -> str:
        """Synchronous alias for :meth:`drilldown`."""
        return self.drilldown(handle, selector, inject=inject, parent_id=parent_id)

    # ------------------------------------------------------------------
    # Core pipeline
    # ------------------------------------------------------------------

    def _build(
        self,
        phase: Phase = Phase.answer,
        query: str = "",
        query_tags: list[str] | None = None,
        header: str = "",
        footer: str = "",
        budget_tokens: int | None = None,
        hints: list[str] | None = None,
        extra: dict[str, Any] | None = None,
        explain: bool = False,
    ) -> tuple[ContextPack, ContextBuildExplanation | None]:
        """Run the full context compilation pipeline (synchronous core).

        All eight pipeline steps are pure computation, so no ``await`` is
        needed.  Both :meth:`build` (async) and :meth:`build_sync` delegate
        here.

        Args:
            phase: Active execution phase.
            query: User query string used for relevance scoring.
            query_tags: Optional tag list to boost tag-matched items.
            header: Optional prompt header text.
            footer: Optional prompt footer text.
            budget_tokens: Override the default phase budget.
            hints: Additional hint tags for scoring.
            extra: Reserved for future pipeline extensions.
            explain: When ``True``, collect explanation-specific
                intermediate state and return a populated
                :class:`ContextBuildExplanation`.  When ``False``
                (default), skip explanation overhead and return
                ``None`` as the second tuple element (issue #291).

        Returns:
            A 2-tuple ``(pack, explanation)``.  *explanation* is
            ``None`` when *explain* is ``False``.
        """
        _ = extra  # reserved
        _tags = sorted(set(list(query_tags or []) + list(hints or [])))

        # Override budget if requested
        effective_budget = self._budget
        if budget_tokens is not None:
            effective_budget = ContextBudget(
                route=budget_tokens if phase == Phase.route else self._budget.route,
                call=budget_tokens if phase == Phase.call else self._budget.call,
                interpret=budget_tokens if phase == Phase.interpret else self._budget.interpret,
                answer=budget_tokens if phase == Phase.answer else self._budget.answer,
            )

        # 1. Generate candidates
        candidates = generate_candidates(self._event_log, phase, self._policy)

        # 2. Dependency closure
        if explain:
            pre_closure_ids = {c.id for c in candidates}
        candidates, closures = resolve_dependency_closure(candidates, self._event_log)
        closure_added_ids = {c.id for c in candidates} - pre_closure_ids if explain else set()

        # 3. Sensitivity filter
        if explain:
            pre_sens_ids = {(c.id, c.kind.value, c.sensitivity.value) for c in candidates}
        candidates, sensitivity_drops = apply_sensitivity_filter(candidates, self._policy)
        if explain:
            post_sens_ids = {c.id for c in candidates}
            sensitivity_dropped_records: list[tuple[str, str, str]] = sorted(
                (cid, kind, sens)
                for (cid, kind, sens) in pre_sens_ids
                if cid not in post_sens_ids
            )
        else:
            sensitivity_dropped_records = []

        # 4. Firewall
        candidates, envelopes = apply_firewall_to_batch(
            candidates,
            self._artifact_store,
            self._hook,
            summarizer=self._summarizer,
            extractor=self._extractor,
        )

        # 5. Score
        scored = score_candidates(candidates, query, _tags, self._scoring)

        # 6. Dedup
        if explain:
            pre_dedup_view: list[tuple[str, str, str, float]] = [
                (item.id, item.kind.value, item.sensitivity.value, score)
                for score, item in scored
            ]
        scored, dedup_removed = deduplicate_candidates(
            scored, similarity_threshold=self._scoring.dedup_threshold
        )
        if explain:
            post_dedup_ids = {item.id for _score, item in scored}
            dedup_dropped_records: list[tuple[str, str, str, float]] = [
                (iid, kind, sens, sc)
                for (iid, kind, sens, sc) in pre_dedup_view
                if iid not in post_dedup_ids
            ]
        else:
            dedup_dropped_records = []

        # Pre-build episodic + fact injection text so we can estimate its
        # token cost and subtract it from the budget *before* selection.
        extra_sections: list[str] = []

        # Episodic summaries (latest 3)
        episodic_entries = self._episodic_store.latest(3)
        if episodic_entries:
            ep_lines = ["[EPISODIC MEMORY]"]
            for _ep_id, ep_summary, _meta in episodic_entries:
                ep_lines.append(f"- {ep_summary}")
            extra_sections.append("\n".join(ep_lines))

        # Facts snapshot — capped to avoid unbounded prompt growth.
        all_facts = self._fact_store.all()
        if all_facts:
            fact_lines: list[str] = ["[FACTS]"]
            total_chars = len(fact_lines[0])
            for idx, fact in enumerate(all_facts):
                if idx >= _MAX_FACT_LINES:
                    remaining = len(all_facts) - idx
                    if remaining > 0:
                        fact_lines.append(f"- ... ({remaining} more facts omitted)")
                    break
                line = f"- {fact.key}: {fact.value}"
                if total_chars + len(line) > _MAX_FACT_CHARS:
                    fact_lines.append("- ... (facts truncated to fit header budget)")
                    break
                fact_lines.append(line)
                total_chars += len(line)
            extra_sections.append("\n".join(fact_lines))

        # Build full header with injected sections
        full_header = header
        if extra_sections:
            prefix = "\n\n".join(extra_sections)
            full_header = f"{prefix}\n\n{header}" if header else prefix

        # Estimate token cost of header/footer so we can reserve budget.
        hf_tokens = 0
        if full_header:
            hf_tokens += self._estimator.estimate(full_header)
        if footer:
            hf_tokens += self._estimator.estimate(footer)

        # Subtract header/footer overhead from the effective budget so that
        # select_and_pack only fills the remaining space.
        if hf_tokens > 0:
            adjusted = ContextBudget(
                route=max(effective_budget.route - hf_tokens, 0)
                if phase == Phase.route
                else effective_budget.route,
                call=max(effective_budget.call - hf_tokens, 0)
                if phase == Phase.call
                else effective_budget.call,
                interpret=max(effective_budget.interpret - hf_tokens, 0)
                if phase == Phase.interpret
                else effective_budget.interpret,
                answer=max(effective_budget.answer - hf_tokens, 0)
                if phase == Phase.answer
                else effective_budget.answer,
            )
        else:
            adjusted = effective_budget

        # 7. Select (budget already accounts for header/footer overhead)
        selected, stats = select_and_pack(scored, phase, adjusted, self._policy, self._estimator)
        stats.dedup_removed = dedup_removed
        stats.dependency_closures = closures
        stats.header_footer_tokens = hf_tokens
        if sensitivity_drops > 0:
            # Account for items dropped by sensitivity filtering in both the
            # total candidate count and the drop breakdown so that
            # dropped_count + included_count <= total_candidates remains true.
            stats.total_candidates += sensitivity_drops
            stats.dropped_count += sensitivity_drops
            stats.dropped_reasons["sensitivity"] = (
                stats.dropped_reasons.get("sensitivity", 0) + sensitivity_drops
            )

        # 8. Render
        prompt = render_context(selected, header=full_header, footer=footer)

        pack = ContextPack(prompt=prompt, stats=stats, phase=phase, envelopes=envelopes)

        # Assemble the explanation (issue #291) only when requested.
        explanation: ContextBuildExplanation | None = None
        if explain:
            explanation = _build_explanation(
                phase=phase,
                query=query,
                stats=stats,
                sensitivity_dropped=sensitivity_dropped_records,
                sensitivity_drops=sensitivity_drops,
                dedup_dropped=dedup_dropped_records,
                dedup_removed=dedup_removed,
                closures=closures,
                closure_added_ids=closure_added_ids,
                scored=scored,
                selected_ids={item.id for item in selected},
                budget_tokens=adjusted.for_phase(phase),
            )

        self._hook.on_context_built(pack)
        logger.info(
            "context build: phase=%s, included=%d, dropped=%d, tokens=%d/%d",
            phase.value,
            stats.included_count,
            stats.dropped_count,
            sum(stats.tokens_per_section.values()),
            effective_budget.for_phase(phase),
        )
        return pack, explanation

    @overload
    async def build(
        self,
        phase: Phase = ...,
        query: str = ...,
        query_tags: list[str] | None = ...,
        header: str = ...,
        footer: str = ...,
        budget_tokens: int | None = ...,
        hints: list[str] | None = ...,
        extra: dict[str, Any] | None = ...,
        *,
        explain: Literal[False] = False,
    ) -> ContextPack: ...

    @overload
    async def build(
        self,
        phase: Phase = ...,
        query: str = ...,
        query_tags: list[str] | None = ...,
        header: str = ...,
        footer: str = ...,
        budget_tokens: int | None = ...,
        hints: list[str] | None = ...,
        extra: dict[str, Any] | None = ...,
        *,
        explain: Literal[True],
    ) -> tuple[ContextPack, ContextBuildExplanation]: ...

    async def build(
        self,
        phase: Phase = Phase.answer,
        query: str = "",
        query_tags: list[str] | None = None,
        header: str = "",
        footer: str = "",
        budget_tokens: int | None = None,
        hints: list[str] | None = None,
        extra: dict[str, Any] | None = None,
        *,
        explain: bool = False,
    ) -> ContextPack | tuple[ContextPack, ContextBuildExplanation]:
        """Asynchronously compile a :class:`~contextweaver.envelope.ContextPack`.

        The current pipeline is fully synchronous; this ``async`` wrapper
        exists so callers can ``await`` it today and benefit from true
        async I/O if the pipeline gains ``await``-able steps in the future.

        See :meth:`_build` for full parameter documentation.

        Args:
            phase: Active execution phase.
            query: User query string used for relevance scoring.
            query_tags: Optional tag list to boost tag-matched items.
            header: Optional prompt header text.
            footer: Optional prompt footer text.
            budget_tokens: Override the default phase budget.
            hints: Additional hint tags for scoring.
            extra: Reserved for future pipeline extensions.
            explain: Keyword-only.  When ``True``, the method returns a
                ``(pack, explanation)`` tuple where *explanation* is a
                :class:`ContextBuildExplanation` capturing per-candidate
                scoring + drop reasons.  Default ``False`` preserves the
                bare :class:`ContextPack` return shape (issue #291).

        Returns:
            A :class:`~contextweaver.envelope.ContextPack` ready for
            the LLM, or a ``(pack, explanation)`` tuple when
            ``explain=True``.
        """
        pack, explanation = self._build(
            phase=phase,
            query=query,
            query_tags=query_tags,
            header=header,
            footer=footer,
            budget_tokens=budget_tokens,
            hints=hints,
            extra=extra,
            explain=explain,
        )
        return (pack, explanation) if explain else pack

    @overload
    def build_sync(
        self,
        phase: Phase = ...,
        query: str = ...,
        query_tags: list[str] | None = ...,
        header: str = ...,
        footer: str = ...,
        budget_tokens: int | None = ...,
        hints: list[str] | None = ...,
        extra: dict[str, Any] | None = ...,
        *,
        explain: Literal[False] = False,
    ) -> ContextPack: ...

    @overload
    def build_sync(
        self,
        phase: Phase = ...,
        query: str = ...,
        query_tags: list[str] | None = ...,
        header: str = ...,
        footer: str = ...,
        budget_tokens: int | None = ...,
        hints: list[str] | None = ...,
        extra: dict[str, Any] | None = ...,
        *,
        explain: Literal[True],
    ) -> tuple[ContextPack, ContextBuildExplanation]: ...

    def build_sync(
        self,
        phase: Phase = Phase.answer,
        query: str = "",
        query_tags: list[str] | None = None,
        header: str = "",
        footer: str = "",
        budget_tokens: int | None = None,
        hints: list[str] | None = None,
        extra: dict[str, Any] | None = None,
        *,
        explain: bool = False,
    ) -> ContextPack | tuple[ContextPack, ContextBuildExplanation]:
        """Synchronous entry point — delegates to :meth:`_build`.

        Works inside Jupyter notebooks, FastAPI handlers, and any other
        environment where an event loop is already running.

        See :meth:`_build` for full parameter documentation.

        Args:
            phase: Active execution phase.
            query: User query string used for relevance scoring.
            query_tags: Optional tag list to boost tag-matched items.
            header: Optional prompt header text.
            footer: Optional prompt footer text.
            budget_tokens: Override the default phase budget.
            hints: Additional hint tags for scoring.
            extra: Reserved for future pipeline extensions.
            explain: Keyword-only.  Same semantics as
                :meth:`build` (issue #291).

        Returns:
            A :class:`~contextweaver.envelope.ContextPack` ready for
            the LLM, or a ``(pack, explanation)`` tuple when
            ``explain=True``.
        """
        pack, explanation = self._build(
            phase=phase,
            query=query,
            query_tags=query_tags,
            header=header,
            footer=footer,
            budget_tokens=budget_tokens,
            hints=hints,
            extra=extra,
            explain=explain,
        )
        return (pack, explanation) if explain else pack

    # ------------------------------------------------------------------
    # Route-integrated build
    # ------------------------------------------------------------------

    def build_route_prompt(
        self,
        goal: str,
        query: str,
        router: Router,
        budget_tokens: int | None = None,
        *,
        history: RouteHistory | None = None,
        history_from_log: bool = True,
    ) -> tuple[ContextPack, list[ChoiceCard], RouteResult]:
        """Route, build context, and assemble a prompt with choice cards.

        Runs the router to find the best tools for *query*, then builds a
        :class:`ContextPack` for the ``route`` phase with choice cards
        appended.

        Args:
            goal: High-level goal description.
            query: User query string.
            router: The :class:`Router` to use for tool routing.
            budget_tokens: Optional budget override.
            history: Optional pre-built :class:`RouteHistory` (issue #27).
                When ``None`` and *history_from_log* is ``True``, the
                manager auto-constructs one from its event log.
            history_from_log: When ``True`` (default) and *history* is
                ``None``, build a :class:`RouteHistory` from the event log
                — already-called tools are deprioritised on subsequent
                routing calls in the same session.  Set to ``False`` to
                preserve pre-#27 stateless routing.

        Returns:
            A 3-tuple ``(pack, cards, route_result)``.
        """
        from contextweaver.routing.cards import make_choice_cards, render_cards_text

        if history is None and history_from_log:
            history = self._build_route_history_from_log()
        route_result = (
            router.route(query, history=history) if history is not None else router.route(query)
        )

        # Build choice cards from route results
        cards = make_choice_cards(
            route_result.candidate_items,
            scores={
                cid: score
                for cid, score in zip(route_result.candidate_ids, route_result.scores, strict=False)
            },
        )

        # Render cards as text for the prompt footer
        cards_text = render_cards_text(cards)
        footer = f"[AVAILABLE TOOLS]\n{cards_text}" if cards_text else ""

        pack, _explanation = self._build(
            phase=Phase.route,
            query=query,
            header=f"[GOAL]\n{goal}",
            footer=footer,
            budget_tokens=budget_tokens,
        )

        self._hook.on_route_completed(route_result.candidate_ids)
        if self._metrics is not None:
            self._metrics.record_route(route_result)
        return pack, cards, route_result

    def build_route_prompt_sync(
        self,
        goal: str,
        query: str,
        router: Router,
        budget_tokens: int | None = None,
        *,
        history: RouteHistory | None = None,
        history_from_log: bool = True,
    ) -> tuple[ContextPack, list[ChoiceCard], RouteResult]:
        """Synchronous alias for :meth:`build_route_prompt`."""
        return self.build_route_prompt(
            goal,
            query,
            router,
            budget_tokens,
            history=history,
            history_from_log=history_from_log,
        )

    def _build_route_history_from_log(self) -> RouteHistory | None:
        """Construct a :class:`RouteHistory` from the event log (issue #27).

        Returns ``None`` when the log contains no ``tool_result`` entries
        (the very first routing call in a session) so the router runs in
        pre-#27 stateless mode.

        The summary is the most recent ``tool_result`` body truncated to
        500 characters — the same heuristic suggested in the issue body.
        ``called_tool_ids`` is derived from each ``tool_result``'s
        originating tool call's ``function_name`` metadata — the canonical
        catalog item id.  Resolution order:

        1. ``tool_result.metadata["function_name"]`` (Gemini adapter sets
           this directly on the result).
        2. Resolve the parent ``tool_call`` item via
           ``event_log.get(parent_id)`` and read its
           ``metadata["function_name"]`` (OpenAI / Anthropic adapters).
        3. Fallback: use ``parent_id`` verbatim (backward-compatible for
           simple event logs where ``parent_id`` *is* the tool id).
        """
        from contextweaver.routing.history import RouteHistory as _RouteHistory

        items = self._event_log.all()
        tool_results = [i for i in items if i.kind == ItemKind.tool_result]
        if not tool_results:
            return None
        called_ids: list[str] = []
        seen: set[str] = set()
        for item in tool_results:
            tid = self._resolve_tool_id_from_result(item)
            if tid in seen:
                continue
            seen.add(tid)
            called_ids.append(tid)
        last = tool_results[-1]
        summary = (last.text or "")[:500] or None
        return _RouteHistory(
            called_tool_ids=called_ids,
            last_result_summary=summary,
            step_number=len(called_ids) + 1,
        )

    def _resolve_tool_id_from_result(self, item: ContextItem) -> str:
        """Derive the catalog tool id from a tool_result ContextItem.

        Resolution order:
        1. item.metadata["function_name"] (set by Gemini adapter)
        2. Parent tool_call item's metadata["function_name"] (OpenAI/Anthropic)
        3. Fallback to parent_id (backward-compat for simple logs)
        """
        meta = item.metadata or {}
        fn = meta.get("function_name")
        if isinstance(fn, str) and fn:
            return fn
        parent_id = item.parent_id or item.id
        try:
            parent = self._event_log.get(parent_id)
            parent_meta = parent.metadata or {}
            parent_fn = parent_meta.get("function_name")
            if isinstance(parent_fn, str) and parent_fn:
                return parent_fn
        except Exception:  # noqa: BLE001 — ItemNotFoundError or any store issue
            pass
        return parent_id

    # ------------------------------------------------------------------
    # Call-phase prompt (schema injection)
    # ------------------------------------------------------------------

    def _build_call_prompt(
        self,
        tool_id: str,
        query: str,
        catalog: Catalog,
        schema: dict[str, Any] | None = None,
        examples: list[str] | None = None,
        constraints: dict[str, Any] | None = None,
        budget_tokens: int | None = None,
    ) -> ContextPack:
        """Build a ``Phase.call`` prompt with the tool's full schema injected.

        Hydrates the selected tool from *catalog*, delegates header assembly
        to :func:`~contextweaver.context.call_prompt.build_schema_header`,
        then runs the standard context pipeline with the call-phase budget.

        Args:
            tool_id: ID of the tool selected during routing.
            query: User query string for relevance scoring.
            catalog: The :class:`Catalog` containing *tool_id*.
            schema: Override schema dict (replaces hydrated ``args_schema``
                in the prompt; hydration still occurs for item metadata).
            examples: Override example strings (replaces hydrated examples
                in the prompt; hydration still occurs for item metadata).
            constraints: Override constraints dict (replaces hydrated
                ``constraints`` in the prompt; hydration still occurs for
                item metadata).
            budget_tokens: Override the default ``Phase.call`` budget.

        Returns:
            A :class:`~contextweaver.envelope.ContextPack` for ``Phase.call``.

        Raises:
            ItemNotFoundError: If *tool_id* is not in *catalog*.
        """
        from contextweaver.context.call_prompt import build_schema_header

        hydration = catalog.hydrate(tool_id)
        header = build_schema_header(
            hydration,
            schema=schema,
            examples=examples,
            constraints=constraints,
        )

        pack, _explanation = self._build(
            phase=Phase.call,
            query=query,
            header=header,
            budget_tokens=budget_tokens,
        )
        return pack

    async def build_call_prompt(
        self,
        tool_id: str,
        query: str,
        catalog: Catalog,
        schema: dict[str, Any] | None = None,
        examples: list[str] | None = None,
        constraints: dict[str, Any] | None = None,
        budget_tokens: int | None = None,
    ) -> ContextPack:
        """Async wrapper for :meth:`_build_call_prompt`.

        See :meth:`_build_call_prompt` for parameter documentation.
        """
        return self._build_call_prompt(
            tool_id=tool_id,
            query=query,
            catalog=catalog,
            schema=schema,
            examples=examples,
            constraints=constraints,
            budget_tokens=budget_tokens,
        )

    def build_call_prompt_sync(
        self,
        tool_id: str,
        query: str,
        catalog: Catalog,
        schema: dict[str, Any] | None = None,
        examples: list[str] | None = None,
        constraints: dict[str, Any] | None = None,
        budget_tokens: int | None = None,
    ) -> ContextPack:
        """Synchronous alias for :meth:`_build_call_prompt`.

        See :meth:`_build_call_prompt` for parameter documentation.
        """
        return self._build_call_prompt(
            tool_id=tool_id,
            query=query,
            catalog=catalog,
            schema=schema,
            examples=examples,
            constraints=constraints,
            budget_tokens=budget_tokens,
        )

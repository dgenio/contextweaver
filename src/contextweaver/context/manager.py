"""High-level Context Engine manager for contextweaver.

:class:`ContextManager` orchestrates the full context compilation pipeline:

1. :func:`~contextweaver.context.candidates.generate_candidates` — phase filter
2. :func:`~contextweaver.context.candidates.resolve_dependency_closure` — parent chain expansion
3. :func:`~contextweaver.context.firewall.apply_firewall_to_batch` — raw output interception
4. :func:`~contextweaver.context.scoring.score_candidates` — relevance scoring
5. :func:`~contextweaver.context.dedup.deduplicate_candidates` — near-duplicate removal
6. :func:`~contextweaver.context.selection.select_and_pack` — budget-aware selection
7. :func:`~contextweaver.context.prompt.render_context` — prompt assembly
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from contextweaver.config import ContextBudget, ContextPolicy, ScoringConfig
from contextweaver.context.candidates import generate_candidates, resolve_dependency_closure
from contextweaver.context.dedup import deduplicate_candidates
from contextweaver.context.firewall import apply_firewall, apply_firewall_to_batch
from contextweaver.context.prompt import render_context
from contextweaver.context.scoring import score_candidates
from contextweaver.context.selection import select_and_pack
from contextweaver.envelope import ContextPack, ResultEnvelope
from contextweaver.protocols import (
    ArtifactStore,
    CharDivFourEstimator,
    EventHook,
    EventLog,
    NoOpHook,
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
    from contextweaver.routing.router import Router, RouteResult


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
    ) -> None:
        _stores = stores or StoreBundle()
        self._event_log: EventLog = event_log or _stores.event_log or InMemoryEventLog()
        self._artifact_store: ArtifactStore = (
            artifact_store or _stores.artifact_store or InMemoryArtifactStore()
        )
        self._episodic_store: InMemoryEpisodicStore = (
            _stores.episodic_store or InMemoryEpisodicStore()
        )
        self._fact_store: InMemoryFactStore = _stores.fact_store or InMemoryFactStore()
        self._budget = budget or ContextBudget()
        self._policy = policy or ContextPolicy()
        self._scoring = scoring_config or ScoringConfig()
        self._estimator: TokenEstimator = estimator or CharDivFourEstimator()
        self._hook: EventHook = hook or NoOpHook()

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
    def episodic_store(self) -> InMemoryEpisodicStore:
        """The underlying episodic store."""
        return self._episodic_store

    @property
    def fact_store(self) -> InMemoryFactStore:
        """The underlying fact store."""
        return self._fact_store

    # ------------------------------------------------------------------
    # Ingestion helpers
    # ------------------------------------------------------------------

    def ingest(self, item: ContextItem) -> None:
        """Append *item* to the event log.

        Args:
            item: The context item to ingest.
        """
        self._event_log.append(item)

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
        in the artifact store and the LLM sees only a summary.

        Args:
            tool_call_id: ID of the originating tool call.
            raw_output: Raw tool output string.
            tool_name: Human-readable tool name.
            media_type: MIME type of the output.
            firewall_threshold: Character threshold above which the firewall
                stores the raw output out-of-band.

        Returns:
            A ``(ContextItem, ResultEnvelope)`` tuple.
        """
        item = ContextItem(
            id=f"result:{tool_call_id}",
            kind=ItemKind.tool_result,
            text=raw_output,
            token_estimate=self._estimator.estimate(raw_output),
            metadata={"tool_name": tool_name, "media_type": media_type},
            parent_id=tool_call_id,
        )

        if len(raw_output) > firewall_threshold:
            processed, envelope = apply_firewall(item, self._artifact_store, self._hook)
            if envelope is None:
                # Shouldn't happen for tool_result items, but be safe
                envelope = ResultEnvelope(status="ok", summary=raw_output[:500])
            self._event_log.append(processed)
            return processed, envelope

        # Small output: still extract facts but no artifact storage
        from contextweaver.summarize.extract import extract_facts

        facts = extract_facts(raw_output, item.metadata)
        envelope = ResultEnvelope(
            status="ok",
            summary=raw_output,
            facts=facts,
            provenance={"source_item_id": item.id, "tool_name": tool_name},
        )
        self._event_log.append(item)
        return item, envelope

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
    ) -> ContextPack:
        """Run the full context compilation pipeline (synchronous core).

        All seven pipeline steps are pure computation, so no ``await`` is
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

        Returns:
            A :class:`~contextweaver.envelope.ContextPack` ready for the LLM.
        """
        _ = extra  # reserved
        _tags = list(query_tags or []) + list(hints or [])

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
        candidates, closures = resolve_dependency_closure(candidates, self._event_log)

        # 3. Firewall
        candidates, envelopes = apply_firewall_to_batch(
            candidates, self._artifact_store, self._hook
        )

        # 4. Score
        scored = score_candidates(candidates, query, _tags, self._scoring)

        # 5. Dedup
        scored, dedup_removed = deduplicate_candidates(scored)

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

        # 6. Select (budget already accounts for header/footer overhead)
        selected, stats = select_and_pack(scored, phase, adjusted, self._policy, self._estimator)
        stats.dedup_removed = dedup_removed
        stats.dependency_closures = closures
        stats.header_footer_tokens = hf_tokens

        # 7. Render
        prompt = render_context(selected, header=full_header, footer=footer)

        pack = ContextPack(prompt=prompt, stats=stats, phase=phase, envelopes=envelopes)
        self._hook.on_context_built(pack)
        return pack

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
    ) -> ContextPack:
        """Asynchronously compile a :class:`~contextweaver.envelope.ContextPack`.

        The current pipeline is fully synchronous; this ``async`` wrapper
        exists so callers can ``await`` it today and benefit from true
        async I/O if the pipeline gains ``await``-able steps in the future.
        """
        return self._build(
            phase=phase,
            query=query,
            query_tags=query_tags,
            header=header,
            footer=footer,
            budget_tokens=budget_tokens,
            hints=hints,
            extra=extra,
        )

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
    ) -> ContextPack:
        """Synchronous entry point — delegates to :meth:`_build`.

        Works inside Jupyter notebooks, FastAPI handlers, and any other
        environment where an event loop is already running.
        """
        return self._build(
            phase=phase,
            query=query,
            query_tags=query_tags,
            header=header,
            footer=footer,
            budget_tokens=budget_tokens,
            hints=hints,
            extra=extra,
        )

    # ------------------------------------------------------------------
    # Route-integrated build
    # ------------------------------------------------------------------

    def build_route_prompt(
        self,
        goal: str,
        query: str,
        router: Router,
        budget_tokens: int | None = None,
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

        Returns:
            A 3-tuple ``(pack, cards, route_result)``.
        """
        from contextweaver.routing.cards import make_choice_cards, render_cards_text

        route_result = router.route(query)

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

        pack = self._build(
            phase=Phase.route,
            query=query,
            header=f"[GOAL]\n{goal}",
            footer=footer,
            budget_tokens=budget_tokens,
        )

        self._hook.on_route_completed(route_result.candidate_ids)
        return pack, cards, route_result

    def build_route_prompt_sync(
        self,
        goal: str,
        query: str,
        router: Router,
        budget_tokens: int | None = None,
    ) -> tuple[ContextPack, list[ChoiceCard], RouteResult]:
        """Synchronous alias for :meth:`build_route_prompt`."""
        return self.build_route_prompt(goal, query, router, budget_tokens)

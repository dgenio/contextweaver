"""High-level Context Engine manager for contextweaver.

ContextManager orchestrates the full context compilation pipeline.
Async-first with sync wrappers.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from contextweaver._utils import tokenize
from contextweaver.config import ContextBudget, ContextPolicy, ScoringConfig
from contextweaver.context.candidates import generate_candidates
from contextweaver.context.dedup import deduplicate_candidates
from contextweaver.context.firewall import apply_firewall
from contextweaver.context.prompt import PromptBuilder, render_context
from contextweaver.context.scoring import score_candidates
from contextweaver.context.selection import select_and_pack
from contextweaver.protocols import (
    CharDivFourEstimator,
    EventHook,
    Extractor,
    NoOpHook,
    Summarizer,
    TokenEstimator,
)
from contextweaver.store import StoreBundle
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.store.episodic import InMemoryEpisodicStore
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.store.facts import InMemoryFactStore
from contextweaver.summarize.extract import StructuredExtractor
from contextweaver.summarize.rules import RuleBasedSummarizer
from contextweaver.types import BuildStats, ContextItem, Phase, ResultEnvelope

if TYPE_CHECKING:
    from contextweaver.routing.cards import ChoiceCard
    from contextweaver.routing.router import Router


@dataclass
class ContextPack:
    """The final output of the Context Engine."""

    rendered_text: str = ""
    included_items: list[ContextItem] = field(default_factory=list)
    excluded_items: list[tuple[str, str]] = field(default_factory=list)
    budget_used: int = 0
    budget_total: int = 0
    artifacts_available: list[str] = field(default_factory=list)
    facts_snapshot: dict[str, str] = field(default_factory=dict)
    episodic_summaries: list[str] = field(default_factory=list)
    phase: Phase = Phase.ANSWER
    stats: BuildStats = field(default_factory=BuildStats)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "rendered_text": self.rendered_text,
            "included_items": [i.to_dict() for i in self.included_items],
            "excluded_items": list(self.excluded_items),
            "budget_used": self.budget_used,
            "budget_total": self.budget_total,
            "artifacts_available": list(self.artifacts_available),
            "facts_snapshot": dict(self.facts_snapshot),
            "episodic_summaries": list(self.episodic_summaries),
            "phase": self.phase.value,
            "stats": self.stats.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextPack:
        """Deserialise from a JSON-compatible dict."""
        return cls(
            rendered_text=data.get("rendered_text", ""),
            included_items=[ContextItem.from_dict(i) for i in data.get("included_items", [])],
            excluded_items=[
                tuple(e)
                for e in data.get("excluded_items", [])  # type: ignore[misc]
            ],
            budget_used=data.get("budget_used", 0),
            budget_total=data.get("budget_total", 0),
            artifacts_available=list(data.get("artifacts_available", [])),
            facts_snapshot=dict(data.get("facts_snapshot", {})),
            episodic_summaries=list(data.get("episodic_summaries", [])),
            phase=Phase(data.get("phase", "answer")),
            stats=BuildStats.from_dict(data.get("stats", {})),
        )


class ContextManager:
    """Orchestrates the full context compilation pipeline."""

    def __init__(
        self,
        stores: StoreBundle | None = None,
        token_estimator: TokenEstimator | None = None,
        summarizer: Summarizer | None = None,
        extractor: Extractor | None = None,
        scoring: ScoringConfig | None = None,
        policy: ContextPolicy | None = None,
        budget: ContextBudget | None = None,
        event_hook: EventHook | None = None,
    ) -> None:
        bundle = stores or StoreBundle()
        self.event_log: InMemoryEventLog = bundle.event_log or InMemoryEventLog()  # type: ignore[assignment]
        _art = bundle.artifact_store or InMemoryArtifactStore()
        self.artifact_store: InMemoryArtifactStore = _art  # type: ignore[assignment]
        _epi = bundle.episodic_store or InMemoryEpisodicStore()
        self.episodic_store: InMemoryEpisodicStore = _epi  # type: ignore[assignment]
        self.fact_store: InMemoryFactStore = bundle.fact_store or InMemoryFactStore()  # type: ignore[assignment]
        self._estimator = token_estimator or CharDivFourEstimator()
        self._summarizer = summarizer or RuleBasedSummarizer()
        self._extractor = extractor or StructuredExtractor()
        self._scoring = scoring or ScoringConfig()
        self._policy = policy or ContextPolicy()
        self._budget = budget or ContextBudget()
        self._hook = event_hook or NoOpHook()

    # --- Ingestion ---

    async def ingest(self, item: ContextItem) -> None:
        """Append to event log."""
        await self.event_log.append(item)

    async def ingest_tool_result(
        self,
        tool_call_id: str,
        raw_output: str | bytes,
        tool_name: str = "",
        media_type: str = "text/plain",
        summarizer: Summarizer | None = None,
        firewall_threshold: int = 2000,
    ) -> tuple[ContextItem, ResultEnvelope]:
        """Context firewall entry point."""
        summ = summarizer or self._summarizer
        item, envelope = await apply_firewall(
            raw_output=raw_output,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            media_type=media_type,
            artifact_store=self.artifact_store,
            summarizer=summ,
            extractor=self._extractor,
            token_estimator=self._estimator,
            firewall_threshold=firewall_threshold,
        )
        await self.event_log.append(item)

        if envelope.artifacts:
            original_size = len(raw_output) if isinstance(raw_output, (str, bytes)) else 0
            self._hook.on_firewall_triggered(item.id, original_size, len(envelope.summary))

        return item, envelope

    async def add_fact(self, key: str, value: str, metadata: dict[str, Any] | None = None) -> None:
        """Store a semantic fact."""
        await self.fact_store.put(key, value, metadata)

    async def add_episode(
        self, episode_id: str, summary: str, metadata: dict[str, Any] | None = None
    ) -> None:
        """Store or update a rolling episodic summary."""
        await self.episodic_store.put(episode_id, summary, metadata)

    # --- Building ---

    async def build(
        self,
        goal: str,
        phase: Phase,
        budget_tokens: int | None = None,
        hints: dict[str, Any] | None = None,
    ) -> ContextPack:
        """Build phase-specific context pack."""
        budget = budget_tokens if budget_tokens is not None else self._budget.for_phase(phase)
        hint_tags = set(hints.get("tags", [])) if hints else set()
        goal_tokens = tokenize(goal)

        # 1. Generate candidates from event log
        all_items = self.event_log.all_sync()
        candidates = generate_candidates(all_items, phase, self._policy)

        total_candidates = len(candidates)

        # 2. Score candidates
        scored = score_candidates(candidates, phase, goal_tokens, hint_tags, budget, self._scoring)

        # 3. Deduplicate
        scored, dedup_removed = deduplicate_candidates(scored)

        # 4. Select and pack with dependency closure
        included, excluded, closures = select_and_pack(scored, budget, self.event_log)

        # 5. Fetch episodic summaries
        episodic_entries = await self.episodic_store.latest(3)
        episodic_summaries = [s for _, s, _ in episodic_entries]

        # 6. Fetch facts
        facts = await self.fact_store.get_all()

        # 7. Render context
        rendered_text, tokens_per_section = render_context(
            included, episodic_summaries, facts, phase
        )

        # 8. Assemble ContextPack
        budget_used = sum(tokens_per_section.values())

        # Collect artifact handles
        artifacts_available = []
        for item in included:
            if item.artifact_ref:
                artifacts_available.append(item.artifact_ref)

        stats = BuildStats(
            tokens_per_section=tokens_per_section,
            total_candidates=total_candidates,
            included_count=len(included),
            dropped_count=len(excluded),
            dropped_reasons=self._count_reasons(excluded),
            dedup_removed=dedup_removed,
            dependency_closures=closures,
        )

        pack = ContextPack(
            rendered_text=rendered_text,
            included_items=included,
            excluded_items=excluded,
            budget_used=budget_used,
            budget_total=budget,
            artifacts_available=artifacts_available,
            facts_snapshot=facts,
            episodic_summaries=episodic_summaries,
            phase=phase,
            stats=stats,
        )

        # 9. Fire event hooks
        self._hook.on_context_built(pack, phase)
        if excluded:
            self._hook.on_items_excluded(excluded)

        return pack

    async def build_route_prompt(
        self,
        goal: str,
        query: str,
        router: Router,
        prompt_builder: PromptBuilder | None = None,
        budget_tokens: int | None = None,
    ) -> tuple[ContextPack, list[ChoiceCard], str]:
        """Convenience: route -> cards -> build context -> build prompt for ROUTE phase."""
        from contextweaver.routing.cards import make_choice_cards

        route_result = router.route(query)
        cards = make_choice_cards(route_result.candidate_items, scores=route_result.scores)
        pack = await self.build(goal, Phase.ROUTE, budget_tokens)
        builder = prompt_builder or PromptBuilder()
        prompt = await builder.build_prompt(goal, Phase.ROUTE, pack, cards)
        return pack, cards, prompt

    def _count_reasons(self, excluded: list[tuple[str, str]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for _, reason in excluded:
            counts[reason] = counts.get(reason, 0) + 1
        return counts

    # --- Sync wrappers ---

    def ingest_sync(self, item: ContextItem) -> None:
        """Sync wrapper for ingest."""
        self._run(self.ingest(item))

    def ingest_tool_result_sync(
        self,
        tool_call_id: str,
        raw_output: str | bytes,
        tool_name: str = "",
        media_type: str = "text/plain",
        summarizer: Summarizer | None = None,
        firewall_threshold: int = 2000,
    ) -> tuple[ContextItem, ResultEnvelope]:
        """Sync wrapper for ingest_tool_result."""
        return self._run(  # type: ignore[no-any-return]
            self.ingest_tool_result(
                tool_call_id,
                raw_output,
                tool_name,
                media_type,
                summarizer,
                firewall_threshold,
            )
        )

    def add_fact_sync(self, key: str, value: str, metadata: dict[str, Any] | None = None) -> None:
        """Sync wrapper for add_fact."""
        self._run(self.add_fact(key, value, metadata))

    def add_episode_sync(
        self, episode_id: str, summary: str, metadata: dict[str, Any] | None = None
    ) -> None:
        """Sync wrapper for add_episode."""
        self._run(self.add_episode(episode_id, summary, metadata))

    def build_sync(
        self,
        goal: str,
        phase: Phase,
        budget_tokens: int | None = None,
        hints: dict[str, Any] | None = None,
    ) -> ContextPack:
        """Sync wrapper for build."""
        return self._run(self.build(goal, phase, budget_tokens, hints))  # type: ignore[no-any-return]

    def build_route_prompt_sync(
        self,
        goal: str,
        query: str,
        router: Router,
        prompt_builder: PromptBuilder | None = None,
        budget_tokens: int | None = None,
    ) -> tuple[ContextPack, list[ChoiceCard], str]:
        """Sync wrapper for build_route_prompt."""
        return self._run(  # type: ignore[no-any-return]
            self.build_route_prompt(goal, query, router, prompt_builder, budget_tokens)
        )

    @staticmethod
    def _run(coro: Any) -> Any:
        """Run a coroutine, handling existing event loops."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            # We're inside an async context — create a new loop in a thread
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()
        else:
            return asyncio.run(coro)

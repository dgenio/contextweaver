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

import asyncio
from typing import Any

from contextweaver.config import ContextBudget, ContextPolicy, ScoringConfig
from contextweaver.context.candidates import generate_candidates, resolve_dependency_closure
from contextweaver.context.dedup import deduplicate_candidates
from contextweaver.context.firewall import apply_firewall_to_batch
from contextweaver.context.prompt import render_context
from contextweaver.context.scoring import score_candidates
from contextweaver.context.selection import select_and_pack
from contextweaver.protocols import CharDivFourEstimator, EventHook, NoOpHook, TokenEstimator
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.types import ContextPack, Phase


class ContextManager:
    """Orchestrates the full context compilation pipeline.

    Args:
        event_log: The event log to compile context from.
        artifact_store: Where raw tool outputs are stored out-of-band.
        budget: Per-phase token budget configuration.
        policy: Context policy (allowed kinds, per-kind limits, etc.).
        scoring_config: Weights for the relevance scorer.
        estimator: Token estimator for items without ``token_estimate``.
        hook: Lifecycle event hook (defaults to :class:`~contextweaver.protocols.NoOpHook`).
    """

    def __init__(
        self,
        event_log: InMemoryEventLog | None = None,
        artifact_store: InMemoryArtifactStore | None = None,
        budget: ContextBudget | None = None,
        policy: ContextPolicy | None = None,
        scoring_config: ScoringConfig | None = None,
        estimator: TokenEstimator | None = None,
        hook: EventHook | None = None,
    ) -> None:
        self._event_log = event_log or InMemoryEventLog()
        self._artifact_store = artifact_store or InMemoryArtifactStore()
        self._budget = budget or ContextBudget()
        self._policy = policy or ContextPolicy()
        self._scoring = scoring_config or ScoringConfig()
        self._estimator = estimator or CharDivFourEstimator()
        self._hook: EventHook = hook or NoOpHook()

    @property
    def event_log(self) -> InMemoryEventLog:
        """The underlying event log."""
        return self._event_log

    @property
    def artifact_store(self) -> InMemoryArtifactStore:
        """The underlying artifact store."""
        return self._artifact_store

    async def build(
        self,
        phase: Phase = Phase.answer,
        query: str = "",
        query_tags: list[str] | None = None,
        header: str = "",
        footer: str = "",
        extra: dict[str, Any] | None = None,
    ) -> ContextPack:
        """Asynchronously compile a :class:`~contextweaver.types.ContextPack`.

        Args:
            phase: Active execution phase.
            query: User query string used for relevance scoring.
            query_tags: Optional tag list to boost tag-matched items.
            header: Optional prompt header text.
            footer: Optional prompt footer text.
            extra: Reserved for future pipeline extensions.

        Returns:
            A :class:`~contextweaver.types.ContextPack` ready for the LLM.
        """
        _ = extra  # reserved
        _tags = query_tags or []

        # 1. Generate candidates
        candidates = generate_candidates(self._event_log, phase, self._policy)

        # 2. Dependency closure
        candidates, closures = resolve_dependency_closure(candidates, self._event_log)

        # 3. Firewall
        candidates, _ = apply_firewall_to_batch(candidates, self._artifact_store, self._hook)

        # 4. Score
        scored = score_candidates(candidates, query, _tags, self._scoring)

        # 5. Dedup
        scored, dedup_removed = deduplicate_candidates(scored)

        # 6. Select
        selected, stats = select_and_pack(
            scored, phase, self._budget, self._policy, self._estimator
        )
        stats.dedup_removed = dedup_removed
        stats.dependency_closures = closures

        # 7. Render
        prompt = render_context(selected, header=header, footer=footer)

        pack = ContextPack(prompt=prompt, stats=stats, phase=phase)
        self._hook.on_context_built(pack)
        return pack

    def build_sync(
        self,
        phase: Phase = Phase.answer,
        query: str = "",
        query_tags: list[str] | None = None,
        header: str = "",
        footer: str = "",
        extra: dict[str, Any] | None = None,
    ) -> ContextPack:
        """Synchronous wrapper around :meth:`build`.

        This method must be called from a synchronous context (i.e. when no
        asyncio event loop is already running).  It cannot be used inside
        ``async def`` functions, Jupyter notebooks, or other environments
        where an event loop is already active.  In those cases, ``await``
        :meth:`build` directly instead.

        Args:
            phase: Active execution phase.
            query: User query string.
            query_tags: Optional tag list.
            header: Optional prompt header.
            footer: Optional prompt footer.
            extra: Reserved for future use.

        Returns:
            A :class:`~contextweaver.types.ContextPack`.

        Raises:
            RuntimeError: If called from within a running event loop.
        """
        return asyncio.run(
            self.build(
                phase=phase,
                query=query,
                query_tags=query_tags,
                header=header,
                footer=footer,
                extra=extra,
            )
        )

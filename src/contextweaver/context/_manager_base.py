"""Shared state contract for the :class:`ContextManager` mixins (issue #101).

:class:`_ManagerState` declares the private instance attributes that
:class:`~contextweaver.context.manager.ContextManager` populates in
``__init__``, plus the ``_build`` core entry point.  The partial-class mixins
(:class:`~contextweaver.context._manager_ingest._IngestMixin`,
:class:`~contextweaver.context._manager_build._BuildMixin`,
:class:`~contextweaver.context._manager_routing._RoutingMixin`) inherit it so
they can reference manager internals without importing the concrete class, and
the delegate pipeline modules (:mod:`~contextweaver.context.build`,
:mod:`~contextweaver.context.route_build`, :mod:`~contextweaver.context.call_prompt`)
type their ``manager`` parameter against it.  ``ContextManager`` inherits it
(via the mixins), so every existing call site still type-checks.  Not part of
the public API.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from contextweaver.config import ContextBudget, ContextPolicy, ScoringConfig
    from contextweaver.context.explanation import ContextBuildExplanation
    from contextweaver.context.views import ViewRegistry
    from contextweaver.envelope import ContextPack
    from contextweaver.metrics import MetricsCollector
    from contextweaver.profiles import Mode, ProfileConfig
    from contextweaver.protocols import (
        ArtifactStore,
        EpisodicStore,
        EventHook,
        EventLog,
        Extractor,
        FactStore,
        Summarizer,
        TokenEstimator,
    )
    from contextweaver.types import Phase


class _ManagerState:
    """Private-attribute + ``_build`` contract shared by the manager mixins."""

    _event_log: EventLog
    _artifact_store: ArtifactStore
    _episodic_store: EpisodicStore
    _fact_store: FactStore
    _budget: ContextBudget
    _policy: ContextPolicy
    _scoring: ScoringConfig
    _estimator: TokenEstimator
    _hook: EventHook
    _view_registry: ViewRegistry
    _summarizer: Summarizer | None
    _extractor: Extractor | None
    _metrics: MetricsCollector | None
    _profile: ProfileConfig | None
    _mode: Mode

    if TYPE_CHECKING:
        # Implemented by ``_BuildMixin``; declared here (type-only, no runtime
        # body) so ``_RoutingMixin`` and the delegate modules can call it.
        def _build(
            self,
            phase: Phase = ...,
            query: str = ...,
            query_tags: list[str] | None = ...,
            header: str = ...,
            footer: str = ...,
            budget_tokens: int | None = ...,
            hints: list[str] | None = ...,
            extra: dict[str, Any] | None = ...,
            explain: bool = ...,
        ) -> tuple[ContextPack, ContextBuildExplanation | None]: ...

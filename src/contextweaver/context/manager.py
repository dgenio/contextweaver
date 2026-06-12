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

The class is a thin orchestrator: its public methods are split across small
*partial-class* mixins (issue #101) to keep this module within the project's
<=300 lines per module guideline.  The mixins (:class:`_IngestMixin`,
:class:`_BuildMixin`, :class:`_RoutingMixin`) are flat, single-level, and not
part of the public API; the heavy logic they delegate to lives in
:mod:`contextweaver.context.ingest`, :mod:`~contextweaver.context.build`,
:mod:`~contextweaver.context.route_build`, and
:mod:`~contextweaver.context.call_prompt`.
"""

from __future__ import annotations

from typing import Any, cast

from contextweaver.config import ContextBudget, ContextPolicy, ScoringConfig
from contextweaver.context import ingest as _ingest
from contextweaver.context._manager_build import _BuildMixin
from contextweaver.context._manager_ingest import _IngestMixin
from contextweaver.context._manager_routing import _RoutingMixin
from contextweaver.context.views import ViewRegistry
from contextweaver.metrics import MetricsCollector
from contextweaver.profiles import Mode, ProfileConfig
from contextweaver.protocols import (
    ArtifactStore,
    EpisodicStore,
    EventHook,
    EventLog,
    Extractor,
    FactStore,
    HeuristicEstimator,
    NoOpHook,
    SensitivityClassifier,
    Summarizer,
    TokenEstimator,
)
from contextweaver.store import StoreBundle
from contextweaver.store._async_to_sync import _LoopThread, is_async_store, to_sync
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.store.episodic import InMemoryEpisodicStore
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.store.facts import InMemoryFactStore


def _to_sync_if_async(store: object, loop: _LoopThread | None) -> object:
    """Return *store* unchanged, or its sync bridge if it is an async backend.

    Async store backends (issue #495) are wrapped so the synchronous pipeline
    can consume them; *loop* drives the bridge and must be set whenever any
    async store is present.
    """
    if is_async_store(store):
        assert loop is not None  # set by ContextManager when any store is async
        return to_sync(cast(Any, store), loop)
    return store


class ContextManager(_IngestMixin, _BuildMixin, _RoutingMixin):
    """Orchestrates the full context compilation pipeline.

    Args:
        event_log: The event log to compile context from.
        artifact_store: Where raw tool outputs are stored out-of-band.
        budget: Per-phase token budget configuration.
        policy: Context policy (allowed kinds, per-kind limits, etc.).
        scoring_config: Weights for the relevance scorer.
        estimator: Token estimator for items without ``token_estimate``.
            Defaults to the dependency-free, script-aware
            :class:`~contextweaver.protocols.HeuristicEstimator` (issue #525):
            accurate for CJK/Kana/Hangul/emoji content offline and identical
            to ``len // 4`` for Latin text.
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
        deterministic: Keyword-only.  When ``True`` (issue #404) the context
            firewall *fails closed*: any build or ingest that would pass data
            through an LLM-backed summariser raises
            :class:`~contextweaver.exceptions.DeterminismError` instead.  The
            default rule-based and structured firewall paths are unaffected.
            Suitable for regulated/financial workloads that must guarantee no
            model touched the data.
        sensitivity_classifier: Keyword-only.  Optional
            :class:`~contextweaver.protocols.SensitivityClassifier` (issue #542)
            applied at the start of the sensitivity stage and to fact/episode
            header content.  It may only *raise* an item's label, never lower it,
            so unlabelled content (e.g. tool results carrying credentials) is
            enforced instead of silently defaulting to ``public``.  ``None``
            (default) disables classification.  See
            :class:`~contextweaver.context.classify.HeuristicSensitivityClassifier`.
        redact_secrets: Keyword-only.  When ``True`` (issue #428) the firewall
            runs a deterministic secret-scrubbing pass over summaries and
            extracted facts before they reach the prompt.  Off by default;
            tightens the sensitivity model without weakening any default.
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
        deterministic: bool = False,
        sensitivity_classifier: SensitivityClassifier | None = None,
        redact_secrets: bool = False,
    ) -> None:
        _stores = stores or StoreBundle()
        resolved_event_log = event_log or _stores.event_log or InMemoryEventLog()
        resolved_artifact_store = (
            artifact_store or _stores.artifact_store or InMemoryArtifactStore()
        )
        resolved_episodic_store = _stores.episodic_store or InMemoryEpisodicStore()
        resolved_fact_store = _stores.fact_store or InMemoryFactStore()
        # Issue #495: accept async store backends. The synchronous pipeline
        # consumes them through an async-to-sync bridge driven by a private loop
        # thread; ``build`` then offloads the pipeline body so the awaited I/O
        # never blocks the caller's event loop. Sync stores are used as-is.
        self._store_loop: _LoopThread | None = None
        self._async_backed: bool = any(
            is_async_store(s)
            for s in (
                resolved_event_log,
                resolved_artifact_store,
                resolved_episodic_store,
                resolved_fact_store,
            )
        )
        if self._async_backed:
            self._store_loop = _LoopThread()
        self._event_log = cast("EventLog", _to_sync_if_async(resolved_event_log, self._store_loop))
        self._artifact_store = cast(
            "ArtifactStore", _to_sync_if_async(resolved_artifact_store, self._store_loop)
        )
        self._episodic_store = cast(
            "EpisodicStore", _to_sync_if_async(resolved_episodic_store, self._store_loop)
        )
        self._fact_store = cast(
            "FactStore", _to_sync_if_async(resolved_fact_store, self._store_loop)
        )
        # Profile fills any unset config; per-arg overrides win.
        if profile is not None:
            budget = budget if budget is not None else profile.budget
            policy = policy if policy is not None else profile.policy
            scoring_config = scoring_config if scoring_config is not None else profile.scoring
        self._budget = budget or ContextBudget()
        self._policy = policy or ContextPolicy()
        self._scoring = scoring_config or ScoringConfig()
        self._estimator: TokenEstimator = estimator or HeuristicEstimator()
        self._hook: EventHook = hook or NoOpHook()
        self._view_registry: ViewRegistry = ViewRegistry()
        self._summarizer: Summarizer | None = summarizer
        self._extractor: Extractor | None = extractor
        self._metrics: MetricsCollector | None = metrics
        self._profile: ProfileConfig | None = profile
        self._mode: Mode = profile.mode if profile is not None else Mode.strict
        self._deterministic: bool = deterministic
        self._sensitivity_classifier: SensitivityClassifier | None = sensitivity_classifier
        self._redact_secrets: bool = redact_secrets
        # Seeded lazily on first add_fact from existing IDs (issue #462), so a
        # pre-populated/persistent fact store does not collide with a counter
        # restarting at 0 across process restarts.
        self._fact_seq: int = 0
        self._fact_seq_seeded: bool = False

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
        """The view registry for auto-generating drilldown views.

        Custom generators registered here apply to **all** ingestion and build
        paths — :meth:`ingest_tool_result`, :meth:`ingest_mcp_result`, and the
        build-time firewall batch (issue #460).
        """
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

    @property
    def budget(self) -> ContextBudget:
        """The active per-phase :class:`~contextweaver.config.ContextBudget`."""
        return self._budget

    @property
    def deterministic(self) -> bool:
        """Whether the firewall fails closed on LLM summarisation (issue #404)."""
        return self._deterministic

    def close(self) -> None:
        """Release resources held for async store backends (issue #495).

        Stops the private loop thread that drives any async-to-sync store
        bridge.  A no-op when every store is synchronous.  Idempotent; safe to
        call even if the manager was never used.  The loop thread is a daemon,
        so failing to call :meth:`close` leaks nothing at process exit — this is
        for prompt, deterministic cleanup (e.g. in tests).
        """
        if self._store_loop is not None:
            self._store_loop.close()
            self._store_loop = None

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
            PolicyViolationError: If the artifact's source item meets the
                sensitivity floor (or was redacted) and
                :attr:`~contextweaver.config.ContextPolicy.allow_redacted_drilldown`
                is ``False`` (issue #451).
        """
        return _ingest.drilldown(
            artifact_store=self._artifact_store,
            event_log=self._event_log,
            estimator=self._estimator,
            handle=handle,
            selector=selector,
            inject=inject,
            parent_id=parent_id,
            policy=self._policy,
        )

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

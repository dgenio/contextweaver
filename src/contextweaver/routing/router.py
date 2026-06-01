"""Beam-search router for contextweaver.

Performs a bounded beam search over a :class:`~contextweaver.routing.graph.ChoiceGraph`
to find the top-k items that best satisfy a user query.

This module also implements the routing API surface added by the v0.3
issue cluster:

* Negative routing (issue #112) — ``exclude_ids`` / ``exclude_tags``
* Conversation hints (issue #116) — ``context_hints``
* Toolset gating (issue #22) — ``allowed_namespaces`` / ``allowed_tags``
* Uncertainty signals (issue #14) — ``RouteResult.is_ambiguous`` and
  ``RouteResult.clarifying_question``
* Structured trace (issue #51) — ``RouteResult.trace``
* Weaver-spec interop (issue #151) — :meth:`RouteResult.to_routing_decision`
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, overload

from contextweaver._utils import BM25Scorer, FuzzyScorer, TfIdfScorer
from contextweaver.envelope import RoutingDecision
from contextweaver.exceptions import ConfigError, RouteError
from contextweaver.profiles import RoutingConfig
from contextweaver.protocols import EmbeddingBackend, Retriever, RoutingScoreProvider
from contextweaver.routing.cards import make_choice_cards
from contextweaver.routing.explanation import explain_route, explain_route_dict
from contextweaver.routing.filters import (
    augment_query,
    filter_items,
    suggest_clarifying_question,
)
from contextweaver.routing.graph import ChoiceGraph
from contextweaver.routing.history import RouteHistory, adjust_scores
from contextweaver.routing.navigator import BeamSearchNavigator, rank_collected
from contextweaver.routing.pipeline import RoutingPipeline
from contextweaver.routing.registry import EngineRegistry, default_registry
from contextweaver.routing.trace import RouteTrace
from contextweaver.types import SelectableItem

logger = logging.getLogger("contextweaver.routing")

# Union of all scorer types Router accepts. ``FuzzyScorer`` is ``None`` when
# the ``contextweaver[retrieval]`` extra is not installed; we widen with
# ``Any`` rather than naming the runtime ``None`` sentinel here.
_ScorerLike = TfIdfScorer | BM25Scorer | Any

# Registry of named backends. ``Router(scorer_backend="bm25")`` constructs
# the corresponding scorer when no explicit instance is provided.
_SCORER_BACKENDS: dict[str, str] = {
    "tfidf": "TfIdfScorer",
    "bm25": "BM25Scorer",
    "fuzzy": "FuzzyScorer",
}


def _build_scorer(backend: str) -> _ScorerLike:
    """Construct a scorer instance from a backend name."""
    if backend == "tfidf":
        return TfIdfScorer()
    if backend == "bm25":
        return BM25Scorer()
    if backend == "fuzzy":
        if FuzzyScorer is None:
            raise ConfigError(
                "scorer_backend='fuzzy' requires the [retrieval] extra: "
                "pip install 'contextweaver[retrieval]'"
            )
        return FuzzyScorer()
    raise ConfigError(f"Unknown scorer_backend {backend!r}")


# ---------------------------------------------------------------------------
# RouteResult
# ---------------------------------------------------------------------------


@dataclass
class RouteResult:
    """Structured result of a routing query.

    Attributes:
        candidate_items: Ranked list of matched items (at most *top_k*).
        candidate_ids: Corresponding item IDs in ranked order.
        paths: The full beam-search paths taken to reach each candidate.
        scores: Score for each candidate (same order).
        is_ambiguous: ``True`` when the gap between rank-1 and rank-2
            scores is below the router's *confidence_gap* threshold
            (issue #14).
        clarifying_question: Optional disambiguation prompt suggested
            when *is_ambiguous* is ``True`` (issue #14).
        excluded_count: Items filtered by ``exclude_ids`` / ``exclude_tags``
            before scoring (issue #112).
        gated_count: Items filtered by ``allowed_namespaces`` /
            ``allowed_tags`` toolset gating before scoring (issue #22).
        context_hints: Conversation context hints applied to the scoring
            query for this call (issue #116).  Empty list when no hints
            were supplied.
        context_boost_applied: ``True`` when *context_hints* contains at
            least one non-blank hint that altered the scoring query.
        history_adjustments: When a :class:`RouteHistory` is passed to
            :meth:`Router.route`, this maps each candidate item id to the
            net score delta the history-aware re-ranking applied (issue #27).
            Empty dict when no history was supplied or no adjustments fired.
        trace: Structured audit record of the routing call (issue #51).
            Always populated; ``trace.steps`` is non-empty only when
            ``debug=True``.
    """

    candidate_items: list[SelectableItem] = field(default_factory=list)
    candidate_ids: list[str] = field(default_factory=list)
    paths: list[list[str]] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)
    is_ambiguous: bool = False
    clarifying_question: str | None = None
    excluded_count: int = 0
    gated_count: int = 0
    context_hints: list[str] = field(default_factory=list)
    context_boost_applied: bool = False
    history_adjustments: dict[str, float] = field(default_factory=dict)
    trace: RouteTrace = field(default_factory=RouteTrace)

    @property
    def debug_trace(self) -> list[dict[str, Any]]:
        """Legacy view of :attr:`trace` in the pre-#51 dict-of-dicts shape."""
        return self.trace.to_legacy_dicts()

    def to_dict(self, *, include_items: bool = True) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict (issue #289).

        Mirrors the :meth:`to_dict` / :meth:`from_dict` pattern that every
        other public result dataclass in the routing module follows
        (``RouteTrace``, ``RouteHistory``, ``ChoiceCard``, ``RoutingDecision``,
        ``SelectableItem``).

        Args:
            include_items: When ``True`` (default) embed each
                :class:`SelectableItem` as a full dict via
                :meth:`SelectableItem.to_dict`.  When ``False`` emit only the
                ``candidate_ids`` list, which is cheaper to persist and
                avoids carrying ``args_schema`` content into structured logs
                — useful when the catalog is the canonical source of truth
                and the result is being persisted alongside it.

        Returns:
            A deterministic JSON-compatible dict.  Round-trips through
            :meth:`from_dict` (when ``include_items=True``) without loss; in
            ID-only mode the inverse path leaves ``candidate_items`` empty
            because the items are not embedded.
        """
        payload: dict[str, Any] = {
            "candidate_ids": list(self.candidate_ids),
            "paths": [list(p) for p in self.paths],
            "scores": [float(s) for s in self.scores],
            "is_ambiguous": self.is_ambiguous,
            "clarifying_question": self.clarifying_question,
            "excluded_count": self.excluded_count,
            "gated_count": self.gated_count,
            "context_hints": list(self.context_hints),
            "context_boost_applied": self.context_boost_applied,
            "history_adjustments": {k: float(v) for k, v in self.history_adjustments.items()},
            "trace": self.trace.to_dict(),
        }
        if include_items:
            payload["candidate_items"] = [item.to_dict() for item in self.candidate_items]
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RouteResult:
        """Deserialise from a JSON-compatible dict (issue #289).

        Tolerates the ID-only payload produced by
        :meth:`to_dict(include_items=False) <to_dict>`: the resulting
        :class:`RouteResult` has the metadata fields populated but an empty
        :attr:`candidate_items`.  Callers who need the full items must
        re-resolve them against their catalog.

        Missing keys fall back to dataclass defaults so older payloads
        round-trip cleanly when the schema is extended.

        Args:
            data: A dict previously produced by :meth:`to_dict`.

        Returns:
            A :class:`RouteResult` reconstructed from *data*.
        """
        items_raw = data.get("candidate_items", [])
        items = [SelectableItem.from_dict(raw) for raw in items_raw]
        trace_raw = data.get("trace")
        trace = RouteTrace.from_dict(trace_raw) if isinstance(trace_raw, dict) else RouteTrace()
        return cls(
            candidate_items=items,
            candidate_ids=list(data.get("candidate_ids", [])),
            paths=[list(p) for p in data.get("paths", [])],
            scores=[float(s) for s in data.get("scores", [])],
            is_ambiguous=bool(data.get("is_ambiguous", False)),
            clarifying_question=data.get("clarifying_question"),
            excluded_count=int(data.get("excluded_count", 0)),
            gated_count=int(data.get("gated_count", 0)),
            context_hints=list(data.get("context_hints", [])),
            context_boost_applied=bool(data.get("context_boost_applied", False)),
            history_adjustments={
                str(k): float(v) for k, v in data.get("history_adjustments", {}).items()
            },
            trace=trace,
        )

    @overload
    def explanation(self, format: Literal["md"] = "md") -> str: ...  # noqa: A002
    @overload
    def explanation(self, format: Literal["dict"]) -> dict[str, Any]: ...  # noqa: A002

    def explanation(
        self,
        format: Literal["md", "dict"] = "md",  # noqa: A002 — public API kwarg
    ) -> str | dict[str, Any]:
        """Render a human-readable rationale of the routing decision (issue #226).

        Surfaces top-k candidates with scores, the rank-1/rank-2 confidence
        gap, ambiguity + clarifying question, applied context hints, and the
        excluded/gated filter counts.

        Args:
            format: ``"md"`` for a paste-friendly Markdown string (default);
                ``"dict"`` for the versioned structured payload.

        Returns:
            A markdown string when ``format == "md"``, a dict otherwise.

        Privacy: the explanation surfaces item ids + scores + the original
        query.  It does **not** include ``args_schema`` content or full item
        descriptions.  Use the dict form when attaching to span attributes on
        :class:`~contextweaver.extras.otel.OTelEventHook` and similar
        observability surfaces.
        """
        if format == "dict":
            return explain_route_dict(self)
        return explain_route(self, format="md")

    def to_routing_decision(
        self,
        *,
        decision_id: str | None = None,
        selected_item_id: str | None = None,
        selected_card_id: str | None = None,
        context_summary: str | None = None,
        now: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RoutingDecision:
        """Build a spec-aligned :class:`RoutingDecision` from this result.

        Renders :attr:`candidate_items` into a list of
        :class:`~contextweaver.envelope.ChoiceCard` instances (preserving the
        per-candidate scores) and wraps them in a :class:`RoutingDecision`
        envelope.  Issue #151.

        Args:
            decision_id: Identifier to assign to the decision.  Defaults to a
                freshly minted ``"rd-{uuid4}"`` string when not provided.
            selected_item_id: Optional ID of the item the downstream LLM picked.
            selected_card_id: Optional ID of the :class:`ChoiceCard` containing
                the selected item.  When omitted, populated from
                ``selected_item_id`` if it matches one of the candidates.
            context_summary: Optional brief context summary for audit / debug.
            now: Optional timezone-aware timestamp.  Defaults to
                ``datetime.now(timezone.utc)``.
            metadata: Optional metadata dict.  Merged with router-supplied
                provenance under ``metadata["contextweaver"]``.

        Returns:
            A :class:`RoutingDecision` ready for serialisation or adapter
            mapping to ``weaver_contracts.RoutingDecision``.

        Raises:
            RouteError: If this result has no :attr:`candidate_items`.  The
                weaver-spec contract mandates at least one choice card.
        """
        if not self.candidate_items:
            raise RouteError("RoutingDecision requires at least one candidate item")
        score_map = dict(zip(self.candidate_ids, self.scores, strict=False))
        # ``make_choice_cards`` defaults to ``max_cards=20``; pass an explicit
        # cap so converting a router configured with ``top_k > 20`` does not
        # silently truncate candidates (PR #201 review).
        cards = make_choice_cards(
            self.candidate_items,
            scores=score_map,
            max_cards=max(len(self.candidate_items), 1),
        )
        resolved_card_id = selected_card_id
        if resolved_card_id is None and selected_item_id is not None:
            for card in cards:
                if card.id == selected_item_id:
                    resolved_card_id = card.id
                    break
        timestamp = now if now is not None else datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        meta: dict[str, Any] = {}
        if metadata:
            meta.update(metadata)
        cw_meta: dict[str, Any] = {
            "is_ambiguous": self.is_ambiguous,
            "excluded_count": self.excluded_count,
            "gated_count": self.gated_count,
            "context_boost_applied": self.context_boost_applied,
        }
        if self.context_hints:
            cw_meta["context_hints"] = list(self.context_hints)
        if self.clarifying_question is not None:
            cw_meta["clarifying_question"] = self.clarifying_question
        # Merge router-supplied diagnostics into ``metadata["contextweaver"]``
        # rather than ``setdefault``: if the caller already populated that key
        # with their own dict, ``setdefault`` would silently drop our
        # diagnostics (PR #201 review).
        existing = meta.get("contextweaver")
        if isinstance(existing, dict):
            merged = dict(existing)
            for key, value in cw_meta.items():
                merged.setdefault(key, value)
            meta["contextweaver"] = merged
        else:
            meta["contextweaver"] = cw_meta
        return RoutingDecision(
            id=decision_id if decision_id is not None else f"rd-{uuid.uuid4()}",
            choice_cards=cards,
            timestamp=timestamp,
            selected_item_id=selected_item_id,
            selected_card_id=resolved_card_id,
            context_summary=context_summary,
            metadata=meta,
        )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class _ScorerRetriever:
    """Internal :class:`Retriever` adapter for legacy ``scorer=`` callers.

    Wraps any pre-existing scorer that exposes the ``fit(corpus)`` /
    ``score(query, index)`` shape (e.g. :class:`TfIdfScorer`,
    :class:`BM25Scorer`, :class:`FuzzyScorer`) so the rest of
    :class:`Router` can talk to a single :class:`Retriever` surface
    regardless of how the engine was supplied.
    """

    def __init__(self, scorer: _ScorerLike) -> None:
        self._scorer = scorer
        self._corpus_size = 0

    def fit(self, corpus: list[str]) -> None:
        self._scorer.fit(corpus)
        self._corpus_size = len(corpus)

    def search(self, query: str, top_k: int) -> list[tuple[int, float]]:
        scored = [(i, self._scorer.score(query, i)) for i in range(self._corpus_size)]
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored[: max(0, top_k)]

    def score_one(self, query: str, index: int) -> float:
        if not 0 <= index < self._corpus_size:
            return 0.0
        return self._scorer.score(query, index)


class Router:
    """Beam-search router over a :class:`ChoiceGraph`.

    The router scores each candidate node using a pluggable
    :class:`~contextweaver.protocols.Retriever` (TF-IDF by default).
    Nodes not in the catalog are scored on their label + routing_hint.

    Determinism guarantee: ties are broken by node ID (alphabetical).

    Args:
        graph: The choice graph to route over.
        items: Optional list of catalog items to register immediately.
        scorer: Optional pre-existing scorer (:class:`TfIdfScorer`,
            :class:`BM25Scorer`, or :class:`FuzzyScorer`).  Legacy
            shim — prefer *retriever* or *engine_registry*.  When
            supplied the router wraps the scorer in an internal
            :class:`Retriever` adapter so the engine surface stays
            uniform.
        beam_width: Number of beams to keep at each level (default 2).
        max_depth: Maximum tree depth to traverse (default 8).
        top_k: Maximum number of results to return (default 10).
        confidence_gap: Minimum score gap between rank-1 and rank-2 to
            consider the top pick confident.  Must be in ``[0.0, 1.0]``.
        scorer_backend: Keyword-only.  Name of the scorer to construct
            when neither *retriever* nor *scorer* is supplied: one of
            ``"tfidf"`` (default), ``"bm25"``, or ``"fuzzy"``.  Selecting
            ``"fuzzy"`` requires the ``[retrieval]`` extra; unknown
            backends raise :class:`ConfigError`.
        routing_config: Keyword-only.  Optional :class:`RoutingConfig`
            that sets all routing parameters at once.
        retriever: Keyword-only.  Optional
            :class:`~contextweaver.protocols.Retriever` instance.  Takes
            precedence over *scorer*, *scorer_backend*, and
            *engine_registry*.
        engine_registry: Keyword-only.  Optional
            :class:`~contextweaver.routing.registry.EngineRegistry`
            used to resolve the retriever when *retriever* and *scorer*
            are both ``None`` and *scorer_backend* is the default
            (``"tfidf"``).  Defaults to
            :data:`~contextweaver.routing.registry.default_registry`.
        embedding_backend: Keyword-only.  Optional
            :class:`~contextweaver.protocols.EmbeddingBackend` (issue #8).
            When supplied, the router uses a hybrid embedding + TF-IDF
            :class:`Retriever` for initial candidate scoring.  Requires
            the ``contextweaver[embeddings]`` extra at the call site;
            the core install never imports an embedding library.
            Mutually exclusive with *retriever* — pass an embedding-aware
            :class:`Retriever` directly via *retriever* if both routes
            need finer control.
        pipeline: Keyword-only.  Optional pre-built
            :class:`~contextweaver.routing.pipeline.RoutingPipeline`.
            When supplied, the navigator and reranker stages on the
            pipeline replace the router's bundled defaults; the
            retriever on the pipeline is overridden by the one resolved
            from the other constructor args (so corpus indexing stays a
            single source of truth).  The packer stage is available for
            callers to invoke via ``router.pipeline.pack(...)`` but is
            not called by :meth:`route` itself — cards are produced
            externally by :meth:`RouteResult.to_routing_decision`.
            Issue #56.
        score_provider: Keyword-only.  Optional
            :class:`~contextweaver.protocols.RoutingScoreProvider`
            (issue #318).  When supplied, it adjusts the ranked
            ``(item_id, score)`` pairs after navigation, reranking, and
            history adjustment — typically folding in historical execution
            feedback via
            :class:`~contextweaver.routing.feedback.FeedbackAwareScoreProvider`.
            ``None`` (default) keeps routing purely deterministic and is
            byte-equivalent to pre-#318 behaviour.

    Raises:
        ConfigError: If *confidence_gap* is outside ``[0.0, 1.0]`` or
            if *scorer_backend* is not a recognised backend name, or if
            both *retriever* and *embedding_backend* are supplied.
    """

    def __init__(
        self,
        graph: ChoiceGraph,
        items: list[SelectableItem] | None = None,
        scorer: _ScorerLike | None = None,
        beam_width: int = 2,
        max_depth: int = 8,
        top_k: int = 10,
        confidence_gap: float = 0.15,
        *,
        scorer_backend: str = "tfidf",
        routing_config: RoutingConfig | None = None,
        retriever: Retriever | None = None,
        engine_registry: EngineRegistry | None = None,
        embedding_backend: EmbeddingBackend | None = None,
        pipeline: RoutingPipeline | None = None,
        score_provider: RoutingScoreProvider | None = None,
    ) -> None:
        if routing_config is not None:
            beam_width = routing_config.beam_width
            max_depth = routing_config.max_depth
            top_k = routing_config.top_k
            confidence_gap = routing_config.confidence_gap
        if not 0.0 <= confidence_gap <= 1.0:
            raise ConfigError(f"confidence_gap must be in [0.0, 1.0], got {confidence_gap}")
        if scorer_backend not in _SCORER_BACKENDS:
            raise ConfigError(
                f"Unknown scorer_backend {scorer_backend!r}; "
                f"valid options: {sorted(_SCORER_BACKENDS)}"
            )
        if embedding_backend is not None and retriever is not None:
            raise ConfigError(
                "Pass either retriever= or embedding_backend=, not both. "
                "Construct an embedding-aware Retriever and pass it via retriever= "
                "if you need both signals combined under a custom policy."
            )
        self._graph = graph
        self._beam_width = beam_width
        self._max_depth = max_depth
        self._top_k = top_k
        self._confidence_gap = confidence_gap
        self._scorer_backend = scorer_backend
        self._items: dict[str, SelectableItem] = {}
        self._engine_registry = engine_registry or default_registry
        if retriever is not None:
            self._retriever: Retriever = retriever
            self._retriever_engine_name = self._engine_registry.default_for("retriever") or "tfidf"
        elif embedding_backend is not None:
            # Late import keeps the core install free of any sentence-
            # transformers / hnswlib / torch dependency.  Importing the
            # adapter only happens when a backend is actually supplied.
            from contextweaver.extras.embeddings import HybridEmbeddingRetriever

            self._retriever = HybridEmbeddingRetriever(embedding_backend)
            self._retriever_engine_name = "embedding+tfidf"
        elif scorer is not None:
            self._retriever = _ScorerRetriever(scorer)
            self._retriever_engine_name = "tfidf"
        elif scorer_backend != "tfidf":
            self._retriever = _ScorerRetriever(_build_scorer(scorer_backend))
            self._retriever_engine_name = scorer_backend
        else:
            self._retriever = self._engine_registry.resolve("retriever")
            self._retriever_engine_name = self._engine_registry.default_for("retriever") or "tfidf"
        self._score_provider = score_provider
        self._indexed = False
        self._doc_id_to_idx: dict[str, int] = {}
        self._pipeline = self._build_pipeline(pipeline)
        if items is not None:
            self.set_items(items)

    def _build_pipeline(self, override: RoutingPipeline | None) -> RoutingPipeline:
        """Construct the routing pipeline (issue #56).

        When *override* is supplied, its navigator / packer / reranker
        replace the bundled defaults; the retriever is always set to the
        one this :class:`Router` already resolved so corpus indexing has
        a single source of truth.
        """
        navigator = BeamSearchNavigator(
            beam_width=self._beam_width,
            max_depth=self._max_depth,
            top_k=self._top_k,
            confidence_gap=self._confidence_gap,
        )
        if override is None:
            return RoutingPipeline(
                retriever=self._retriever,
                reranker=None,
                navigator=navigator,
            )
        return RoutingPipeline(
            retriever=self._retriever,
            reranker=override.reranker,
            navigator=override.navigator or navigator,
            packer=override.packer,
        )

    @property
    def pipeline(self) -> RoutingPipeline:
        """The :class:`RoutingPipeline` this router delegates to (issue #56)."""
        return self._pipeline

    def set_items(self, items: list[SelectableItem]) -> None:
        """Register the catalog items for retriever indexing and result lookup.

        Args:
            items: All items in the catalog.
        """
        self._items = {it.id: it for it in items}
        self._indexed = False

    def _ensure_index(self) -> None:
        """Lazily fit the retriever on item + node texts."""
        if self._indexed:
            return

        docs: list[str] = []
        doc_ids: list[str] = []

        for item_id in sorted(self._items):
            item = self._items[item_id]
            docs.append(f"{item.name} {item.description} {' '.join(item.tags)}")
            doc_ids.append(item_id)

        for node_id in self._graph.nodes():
            if node_id not in self._items:
                node = self._graph.get_node(node_id)
                text = f"{node.label} {node.routing_hint}"
                docs.append(text)
                doc_ids.append(node_id)

        self._retriever.fit(docs)
        self._doc_id_to_idx = {did: i for i, did in enumerate(doc_ids)}
        self._indexed = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def route(  # noqa: PLR0913 — multiple optional filters are intentional public API
        self,
        query: str,
        *,
        debug: bool = False,
        exclude_ids: set[str] | None = None,
        exclude_tags: set[str] | None = None,
        allowed_namespaces: set[str] | None = None,
        allowed_tags: set[str] | None = None,
        context_hints: list[str] | None = None,
        history: RouteHistory | None = None,
    ) -> RouteResult:
        """Route *query* through the graph and return ranked results.

        Args:
            query: The user query string.
            debug: When ``True``, populate the per-step beam expansions
                in :attr:`RouteResult.trace`.  The trace itself is always
                populated regardless of *debug*.
            exclude_ids: Optional set of item IDs to drop before scoring
                (issue #112 — negative routing).
            exclude_tags: Optional set of tags; items carrying any of
                these tags are dropped (issue #112 — negative routing).
            allowed_namespaces: Optional whitelist of namespaces.  When
                provided, only items whose ``namespace`` is in the set
                participate in routing (issue #22 — toolset gating).
            allowed_tags: Optional whitelist of tags.  When provided,
                items must share at least one tag with the set
                (issue #22 — toolset gating).
            context_hints: Optional list of conversation context hints.
                Hints are appended to the query for scoring purposes
                (issue #116).  Hints do not change the catalog or graph.
            history: Optional :class:`RouteHistory` (issue #27).  When
                supplied, already-called tools are deprioritised, tools
                semantically related to ``last_result_summary`` are
                boosted, and ``SelectableItem.depends_on`` / ``provides``
                / ``requires`` metadata is applied.  Per-candidate score
                deltas surface on :attr:`RouteResult.history_adjustments`.
                ``None`` is byte-equivalent to pre-#27 behaviour.

        Returns:
            A :class:`RouteResult` with ranked items and a populated
            :class:`~contextweaver.routing.trace.RouteTrace`.

        Raises:
            RouteError: If the graph is empty or no items are registered.
        """
        if not self._items:
            raise RouteError(
                "No items registered. Pass items to Router() or call set_items() before routing."
            )
        root = self._graph.root_id
        if root not in self._graph.nodes():
            raise RouteError(f"Root node {root!r} not in graph.")

        self._ensure_index()
        active_items, excluded_count, gated_count = filter_items(
            self._items,
            exclude_ids=exclude_ids,
            exclude_tags=exclude_tags,
            allowed_namespaces=allowed_namespaces,
            allowed_tags=allowed_tags,
        )
        if not active_items:
            raise RouteError(
                "All items were filtered out by exclude_ids / exclude_tags / "
                "allowed_namespaces / allowed_tags."
            )
        applied_hints = [h.strip() for h in (context_hints or []) if h and h.strip()]
        scoring_query = augment_query(query, context_hints)
        boost_applied = scoring_query != query and bool(applied_hints)

        nav_result = self._pipeline.navigate(
            scoring_query,
            self._graph,
            active_items,
            self._doc_id_to_idx,
            all_item_ids=set(self._items),
            debug=debug,
        )
        collected = nav_result.collected
        steps = nav_result.steps
        all_sorted = rank_collected(collected, active_items)

        # Rerank stage (issue #56): apply the pipeline reranker after
        # navigation scoring.  When reranker is None this is a no-op copy.
        if self._pipeline.reranker is not None and all_sorted:
            id_to_path = {iid: path for iid, (_, path) in all_sorted}
            reranked = self._pipeline.rerank(
                scoring_query,
                [(iid, score) for iid, (score, _) in all_sorted],
            )
            all_sorted = [(iid, (score, id_to_path[iid])) for iid, score in reranked]

        history_adjustments: dict[str, float] = {}
        if history is not None and all_sorted:
            id_to_path = {iid: path for iid, (_, path) in all_sorted}
            scored_pairs = [(iid, score) for iid, (score, _) in all_sorted]
            result_similarity = self._result_similarity_map(history, scored_pairs)
            adjusted, history_adjustments = adjust_scores(
                scored_pairs,
                history,
                self._items,
                result_similarity=result_similarity,
            )
            all_sorted = [(iid, (score, id_to_path[iid])) for iid, score in adjusted]

        # Feedback-aware scoring stage (issue #318): apply the optional
        # score provider last so it has the final word over the ranking.
        # ``None`` (default) leaves ``all_sorted`` untouched, keeping routing
        # purely deterministic and byte-equivalent to pre-#318 behaviour.
        if self._score_provider is not None and all_sorted:
            id_to_path = {iid: path for iid, (_, path) in all_sorted}
            provider_input = [(iid, score) for iid, (score, _) in all_sorted]
            provider_output = self._score_provider.adjust(scoring_query, provider_input)
            all_sorted = [
                (iid, (score, id_to_path[iid]))
                for iid, score in provider_output
                if iid in id_to_path
            ]
        top = all_sorted[: self._top_k]

        # Ambiguity reads from the untrimmed sorted view so that callers
        # using top_k=1 still see the runner-up signal (issue #14).
        top_score = all_sorted[0][1][0] if all_sorted else 0.0
        runner_up_score = all_sorted[1][1][0] if len(all_sorted) >= 2 else None
        is_ambiguous = (
            len(all_sorted) >= 2
            and (all_sorted[0][1][0] - all_sorted[1][1][0]) < self._confidence_gap
        )

        trace = RouteTrace(
            query=query,
            confidence_gap=self._confidence_gap,
            top_score=top_score,
            runner_up_score=runner_up_score,
            excluded_count=excluded_count,
            gated_count=gated_count,
            retriever_engine=self._retriever_engine_name,
            steps=steps if debug else [],
        )
        trace.is_ambiguous = is_ambiguous
        trace.extra["context_hints"] = list(applied_hints)
        trace.extra["context_boost_applied"] = boost_applied
        if self._score_provider is not None:
            trace.extra["score_provider"] = type(self._score_provider).__name__
        if history_adjustments:
            trace.extra["history_adjustments"] = dict(history_adjustments)
        top_items = [active_items[iid] for iid, _ in top if iid in active_items]
        # Clarifying question is rendered from the top of the untrimmed
        # sort so it stays useful when top_k=1.
        clarifying_pool = [active_items[iid] for iid, _ in all_sorted[:3] if iid in active_items]
        clarifying = suggest_clarifying_question(query, clarifying_pool) if is_ambiguous else None
        trace.clarifying_question = clarifying

        result = RouteResult(
            candidate_items=top_items,
            candidate_ids=[iid for iid, _ in top],
            paths=[path for _, (_, path) in top],
            scores=[score for _, (score, _) in top],
            is_ambiguous=is_ambiguous,
            clarifying_question=clarifying,
            excluded_count=excluded_count,
            gated_count=gated_count,
            context_hints=list(applied_hints),
            context_boost_applied=boost_applied,
            history_adjustments=dict(history_adjustments),
            trace=trace,
        )

        if logger.isEnabledFor(logging.INFO):
            logger.info(
                "route: query_len=%d, top_k=%d, candidates=%d, scores=%s, "
                "ambiguous=%s, excluded=%d, gated=%d, history_adjustments=%d",
                len(query),
                self._top_k,
                len(result.candidate_ids),
                [round(s, 4) for s in result.scores[:5]],
                is_ambiguous,
                excluded_count,
                gated_count,
                len(history_adjustments),
            )
        return result

    def _result_similarity_map(
        self,
        history: RouteHistory,
        scored: list[tuple[str, float]],
    ) -> dict[str, float] | None:
        """Per-candidate similarity to ``history.last_result_summary``.

        Reuses the router's fitted retriever so the boost is computed in
        the same scoring space as the primary query.  Returns ``None`` when
        the history has no summary so :func:`adjust_scores` can skip the
        boost stage entirely.
        """
        summary = history.last_result_summary
        if not summary:
            return None
        sims: dict[str, float] = {}
        for item_id, _ in scored:
            idx = self._doc_id_to_idx.get(item_id)
            if idx is None:
                continue
            sims[item_id] = self._retriever.score_one(summary, idx)
        return sims

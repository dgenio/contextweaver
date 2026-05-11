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
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from contextweaver._utils import TfIdfScorer, jaccard, tokenize
from contextweaver.config import RoutingConfig
from contextweaver.exceptions import ConfigError, RouteError
from contextweaver.protocols import Retriever
from contextweaver.routing.filters import (
    augment_query,
    filter_items,
    suggest_clarifying_question,
)
from contextweaver.routing.graph import ChoiceGraph
from contextweaver.routing.registry import EngineRegistry, default_registry
from contextweaver.routing.trace import RouteTrace, TraceStep
from contextweaver.types import SelectableItem

logger = logging.getLogger("contextweaver.routing")

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
    trace: RouteTrace = field(default_factory=RouteTrace)

    @property
    def debug_trace(self) -> list[dict[str, Any]]:
        """Legacy view of :attr:`trace` in the pre-#51 dict-of-dicts shape."""
        return self.trace.to_legacy_dicts()


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class _ScorerRetriever:
    """Internal :class:`Retriever` adapter for legacy ``scorer=`` callers.

    Wraps a pre-existing :class:`TfIdfScorer` so the rest of
    :class:`Router` can talk to a single :class:`Retriever` surface
    regardless of how the engine was supplied.
    """

    def __init__(self, scorer: TfIdfScorer) -> None:
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
        scorer: Optional pre-existing :class:`TfIdfScorer`.  Legacy
            shim — prefer *retriever* or *engine_registry*.  When
            supplied the router wraps the scorer in an internal
            :class:`Retriever` adapter so the engine surface stays
            uniform.
        beam_width: Number of beams to keep at each level (default 2).
        max_depth: Maximum tree depth to traverse (default 8).
        top_k: Maximum number of results to return (default 10).
        confidence_gap: Minimum score gap between rank-1 and rank-2 to
            consider the top pick confident.  Must be in ``[0.0, 1.0]``.
        routing_config: Keyword-only.  Optional :class:`RoutingConfig`
            that sets all routing parameters at once.
        retriever: Keyword-only.  Optional
            :class:`~contextweaver.protocols.Retriever` instance.  Takes
            precedence over *scorer* and *engine_registry*.
        engine_registry: Keyword-only.  Optional
            :class:`~contextweaver.routing.registry.EngineRegistry`
            used to resolve the retriever when *retriever* and *scorer*
            are both ``None``.  Defaults to
            :data:`~contextweaver.routing.registry.default_registry`.

    Raises:
        ConfigError: If *confidence_gap* is outside ``[0.0, 1.0]``.
    """

    def __init__(
        self,
        graph: ChoiceGraph,
        items: list[SelectableItem] | None = None,
        scorer: TfIdfScorer | None = None,
        beam_width: int = 2,
        max_depth: int = 8,
        top_k: int = 10,
        confidence_gap: float = 0.15,
        *,
        routing_config: RoutingConfig | None = None,
        retriever: Retriever | None = None,
        engine_registry: EngineRegistry | None = None,
    ) -> None:
        if routing_config is not None:
            beam_width = routing_config.beam_width
            max_depth = routing_config.max_depth
            top_k = routing_config.top_k
            confidence_gap = routing_config.confidence_gap
        if not 0.0 <= confidence_gap <= 1.0:
            raise ConfigError(f"confidence_gap must be in [0.0, 1.0], got {confidence_gap}")
        self._graph = graph
        self._beam_width = beam_width
        self._max_depth = max_depth
        self._top_k = top_k
        self._confidence_gap = confidence_gap
        self._items: dict[str, SelectableItem] = {}
        self._engine_registry = engine_registry or default_registry
        if retriever is not None:
            self._retriever: Retriever = retriever
        elif scorer is not None:
            self._retriever = _ScorerRetriever(scorer)
        else:
            self._retriever = self._engine_registry.resolve("retriever")
        self._retriever_engine_name = self._engine_registry.default_for("retriever") or "tfidf"
        self._indexed = False
        self._doc_id_to_idx: dict[str, int] = {}
        if items is not None:
            self.set_items(items)

    def set_items(self, items: list[SelectableItem]) -> None:
        """Register the catalog items for retriever indexing and result lookup."""
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

    def _score_node(self, query: str, node_id: str) -> float:
        """Score a single graph node against *query* via the configured retriever."""
        self._ensure_index()
        idx = self._doc_id_to_idx.get(node_id)
        if idx is not None:
            return self._retriever.score_one(query, idx)

        q_tokens = tokenize(query)
        n_tokens = tokenize(node_id.replace(":", " ").replace("_", " ").replace("/", " "))
        return jaccard(q_tokens, n_tokens)

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
        eligible_internals = self._eligible_internals(active_items)
        applied_hints = [h.strip() for h in (context_hints or []) if h and h.strip()]
        scoring_query = augment_query(query, context_hints)
        boost_applied = scoring_query != query and bool(applied_hints)
        steps, collected = self._beam_search(scoring_query, active_items, eligible_internals, debug)
        all_sorted = self._rank_collected(collected, active_items)
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
            trace=trace,
        )

        if logger.isEnabledFor(logging.INFO):
            logger.info(
                "route: query_len=%d, top_k=%d, candidates=%d, scores=%s, "
                "ambiguous=%s, excluded=%d, gated=%d",
                len(query),
                self._top_k,
                len(result.candidate_ids),
                [round(s, 4) for s in result.scores[:5]],
                is_ambiguous,
                excluded_count,
                gated_count,
            )
        return result

    # ------------------------------------------------------------------
    # Beam search internals
    # ------------------------------------------------------------------

    def _eligible_internals(self, active_items: dict[str, SelectableItem]) -> set[str]:
        """Return internal nodes that have at least one descendant in *active_items*.

        Computed via reverse BFS from each active item.  Used by
        :meth:`_beam_search` and :meth:`_expand_subtree` to skip
        children whose entire subtree was filtered out before scoring,
        so exclusions and toolset gating happen pre-search rather than
        only at collection time (issue #112 / #22).
        """
        eligible: set[str] = set()
        queue: list[str] = list(active_items)
        while queue:
            node = queue.pop()
            for parent in self._graph.predecessors(node):
                if parent in eligible:
                    continue
                eligible.add(parent)
                queue.append(parent)
        return eligible

    def _is_eligible_child(
        self,
        child: str,
        active_items: dict[str, SelectableItem],
        eligible_internals: set[str],
    ) -> bool:
        """Return ``True`` if *child* may participate in beam search.

        Leaves must be in *active_items*; internals must reach an
        active descendant.  Children that fail this gate are skipped
        before scoring so they cannot consume beam slots.
        """
        if child in self._items:
            return child in active_items
        return child in eligible_internals

    def _beam_search(
        self,
        query: str,
        active_items: dict[str, SelectableItem],
        eligible_internals: set[str],
        debug: bool,
    ) -> tuple[list[TraceStep], dict[str, tuple[float, list[str]]]]:
        """Run the beam search and return ``(trace_steps, collected)``.

        *active_items* is the post-filter catalog; only IDs in this dict
        are eligible for collection.  *eligible_internals* is the set of
        internal nodes with at least one active descendant — children
        outside this set (and outside *active_items*) are skipped before
        scoring (issue #112 / #22).
        """
        root = self._graph.root_id
        beam: list[tuple[float, list[str]]] = [(0.0, [root])]
        collected: dict[str, tuple[float, list[str]]] = {}
        unexplored: list[tuple[float, str, list[str]]] = []
        steps: list[TraceStep] = []

        for depth in range(self._max_depth):
            if not beam:
                break
            next_beam: list[tuple[float, list[str]]] = []
            for score, path in beam:
                node_id = path[-1]
                children = self._graph.successors(node_id)
                if not children:
                    if node_id in active_items and node_id not in collected:
                        collected[node_id] = (score, path[1:])
                    continue

                scored: list[tuple[float, str]] = []
                for child in children:
                    if not self._is_eligible_child(child, active_items, eligible_internals):
                        continue
                    s = self._score_node(query, child)
                    scored.append((s, child))
                scored.sort(key=lambda x: (-x[0], x[1]))

                keep = self._beam_width
                if len(scored) >= 2 and scored[0][0] - scored[1][0] < self._confidence_gap:
                    keep = self._beam_width + 1

                kept_ids: list[str] = []
                for i, (s, child) in enumerate(scored):
                    new_path = path + [child]
                    if i < keep:
                        kept_ids.append(child)
                        if child in self._items:
                            if child in active_items and child not in collected:
                                collected[child] = (score + s, new_path[1:])
                        else:
                            next_beam.append((score + s, new_path))
                    else:
                        unexplored.append((score + s, child, new_path))

                if debug:
                    steps.append(
                        TraceStep(
                            depth=depth,
                            node=node_id,
                            scored_children=[(cid, cs) for cs, cid in scored],
                            kept=kept_ids,
                        )
                    )

            next_beam.sort(key=lambda x: (-x[0], x[1][-1]))
            beam = next_beam[: self._beam_width]

        # Collect any remaining beam items.
        for score, path in beam:
            node_id = path[-1]
            if node_id in active_items and node_id not in collected:
                collected[node_id] = (score, path[1:])

        # Backtrack into unexplored branches if under-filled.
        unexplored.sort(key=lambda x: (-x[0], x[1]))
        while len(collected) < self._top_k and unexplored:
            u_score, u_node, u_path = unexplored.pop(0)
            if u_node in collected:
                continue
            if u_node in active_items:
                collected[u_node] = (u_score, u_path[1:])
            else:
                current_depth = len(u_path) - 1
                remaining = max(0, self._max_depth - current_depth)
                sub = self._expand_subtree(
                    query,
                    u_node,
                    u_score,
                    u_path,
                    active_items,
                    eligible_internals,
                    max_depth=remaining,
                )
                for sid, (ss, sp) in sub.items():
                    if sid in active_items and sid not in collected:
                        collected[sid] = (ss, sp)

        return steps, collected

    def _rank_collected(
        self,
        collected: dict[str, tuple[float, list[str]]],
        active_items: dict[str, SelectableItem],
    ) -> list[tuple[str, tuple[float, list[str]]]]:
        """Return *collected* sorted by ``(-score, id)``, untrimmed.

        Truncation to ``self._top_k`` is the caller's responsibility so
        ambiguity / runner-up reads can use the full ranking even when
        ``top_k=1`` (issue #14).
        """
        return sorted(
            (entry for entry in collected.items() if entry[0] in active_items),
            key=lambda x: (-x[1][0], x[0]),
        )

    def _expand_subtree(
        self,
        query: str,
        node_id: str,
        base_score: float,
        base_path: list[str],
        active_items: dict[str, SelectableItem],
        eligible_internals: set[str],
        *,
        max_depth: int | None = None,
    ) -> dict[str, tuple[float, list[str]]]:
        """Expand children of *node_id* recursively, collecting items.

        Children outside *active_items* (leaves) or *eligible_internals*
        (internals) are skipped before scoring so excluded subtrees do
        not consume backtracking work (issue #112 / #22).
        """
        depth_limit = max_depth if max_depth is not None else self._max_depth
        result: dict[str, tuple[float, list[str]]] = {}
        stack: list[tuple[float, str, list[str], int]] = [(base_score, node_id, base_path, 0)]
        while stack:
            score, nid, path, depth = stack.pop()
            children = self._graph.successors(nid)
            if not children or depth >= depth_limit:
                if nid in active_items:
                    result[nid] = (score, path[1:])
                continue
            for child in sorted(children):
                if not self._is_eligible_child(child, active_items, eligible_internals):
                    continue
                s = self._score_node(query, child)
                new_path = path + [child]
                if child in self._items:
                    result[child] = (score + s, new_path[1:])
                else:
                    stack.append((score + s, child, new_path, depth + 1))
        return result

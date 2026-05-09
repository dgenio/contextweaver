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
from contextweaver.routing.filters import (
    augment_query,
    filter_items,
    suggest_clarifying_question,
)
from contextweaver.routing.graph import ChoiceGraph
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
    trace: RouteTrace = field(default_factory=RouteTrace)

    @property
    def debug_trace(self) -> list[dict[str, Any]]:
        """Legacy view of :attr:`trace` in the pre-#51 dict-of-dicts shape."""
        return self.trace.to_legacy_dicts()


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class Router:
    """Beam-search router over a :class:`ChoiceGraph`.

    The router scores each candidate node using TF-IDF similarity between
    the query and the text representation of graph nodes.  Nodes not in the
    catalog are scored on their label + routing_hint.

    Determinism guarantee: ties are broken by node ID (alphabetical).

    Args:
        graph: The choice graph to route over.
        items: Optional list of catalog items to register immediately.
        scorer: Optional pre-fitted :class:`TfIdfScorer`.
        beam_width: Number of beams to keep at each level (default 2).
        max_depth: Maximum tree depth to traverse (default 8).
        top_k: Maximum number of results to return (default 10).
        confidence_gap: Minimum score gap between rank-1 and rank-2 to
            consider the top pick confident.  Must be in ``[0.0, 1.0]``.
        routing_config: Keyword-only.  Optional :class:`RoutingConfig`
            that sets all routing parameters at once.

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
        self._scorer = scorer
        self._indexed = False
        self._doc_id_to_idx: dict[str, int] = {}
        if items is not None:
            self.set_items(items)

    def set_items(self, items: list[SelectableItem]) -> None:
        """Register the catalog items for TF-IDF indexing and result lookup."""
        self._items = {it.id: it for it in items}
        self._indexed = False

    def _ensure_index(self) -> None:
        """Lazily fit the TF-IDF scorer on item + node texts."""
        if self._indexed and self._scorer is not None:
            return
        if self._scorer is None:
            self._scorer = TfIdfScorer()

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

        self._scorer.fit(docs)
        self._doc_id_to_idx = {did: i for i, did in enumerate(doc_ids)}
        self._indexed = True

    def _score_node(self, query: str, node_id: str) -> float:
        """Score a single graph node against *query*."""
        self._ensure_index()
        if self._scorer is None:
            raise RouteError("TF-IDF index was not built; call _ensure_index first.")

        idx = self._doc_id_to_idx.get(node_id)
        if idx is not None:
            return self._scorer.score(query, idx)

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
        scoring_query = augment_query(query, context_hints)
        steps_data = self._beam_search(scoring_query, active_items, debug)
        steps, collected = steps_data
        ranked = self._collect_results(collected, active_items)

        trace = RouteTrace(
            query=query,
            confidence_gap=self._confidence_gap,
            top_score=ranked[0][1][0] if ranked else 0.0,
            runner_up_score=ranked[1][1][0] if len(ranked) >= 2 else None,
            excluded_count=excluded_count,
            gated_count=gated_count,
            retriever_engine="tfidf",
            steps=steps if debug else [],
        )
        is_ambiguous = (
            len(ranked) >= 2 and (ranked[0][1][0] - ranked[1][1][0]) < self._confidence_gap
        )
        trace.is_ambiguous = is_ambiguous
        top_items = [active_items[iid] for iid, _ in ranked if iid in active_items]
        clarifying = suggest_clarifying_question(query, top_items[:3]) if is_ambiguous else None
        trace.clarifying_question = clarifying

        result = RouteResult(
            candidate_items=top_items,
            candidate_ids=[iid for iid, _ in ranked],
            paths=[path for _, (_, path) in ranked],
            scores=[score for _, (score, _) in ranked],
            is_ambiguous=is_ambiguous,
            clarifying_question=clarifying,
            excluded_count=excluded_count,
            gated_count=gated_count,
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

    def _beam_search(
        self,
        query: str,
        active_items: dict[str, SelectableItem],
        debug: bool,
    ) -> tuple[list[TraceStep], dict[str, tuple[float, list[str]]]]:
        """Run the beam search and return ``(trace_steps, collected)``.

        *active_items* is the post-filter catalog; only IDs in this dict
        are eligible for collection.
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
                sub = self._expand_subtree(query, u_node, u_score, u_path, max_depth=remaining)
                for sid, (ss, sp) in sub.items():
                    if sid in active_items and sid not in collected:
                        collected[sid] = (ss, sp)

        return steps, collected

    def _collect_results(
        self,
        collected: dict[str, tuple[float, list[str]]],
        active_items: dict[str, SelectableItem],
    ) -> list[tuple[str, tuple[float, list[str]]]]:
        """Sort and trim *collected* into the final ranked tuple list."""
        ranked = sorted(
            (entry for entry in collected.items() if entry[0] in active_items),
            key=lambda x: (-x[1][0], x[0]),
        )
        return ranked[: self._top_k]

    def _expand_subtree(
        self,
        query: str,
        node_id: str,
        base_score: float,
        base_path: list[str],
        *,
        max_depth: int | None = None,
    ) -> dict[str, tuple[float, list[str]]]:
        """Expand children of *node_id* recursively, collecting items."""
        depth_limit = max_depth if max_depth is not None else self._max_depth
        result: dict[str, tuple[float, list[str]]] = {}
        stack: list[tuple[float, str, list[str], int]] = [(base_score, node_id, base_path, 0)]
        while stack:
            score, nid, path, depth = stack.pop()
            children = self._graph.successors(nid)
            if not children or depth >= depth_limit:
                if nid in self._items:
                    result[nid] = (score, path[1:])
                continue
            for child in sorted(children):
                s = self._score_node(query, child)
                new_path = path + [child]
                if child in self._items:
                    result[child] = (score + s, new_path[1:])
                else:
                    stack.append((score + s, child, new_path, depth + 1))
        return result

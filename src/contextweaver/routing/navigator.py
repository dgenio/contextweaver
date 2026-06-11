"""Beam-search navigator extracted from :mod:`router` (issue #56).

Implements the :class:`~contextweaver.protocols.Navigator` protocol so the
routing pipeline can swap, skip, or tune the navigation stage independently
from retrieval and packing.

The behaviour is byte-identical to the pre-refactor :meth:`Router.route`
beam search — :class:`BeamSearchNavigator` is a verbatim move of
``_eligible_internals``, ``_beam_search``, ``_rank_collected``, and
``_expand_subtree``.  Determinism is preserved by tie-breaking on node id
(alphabetical) at every step.

Privacy: the navigator never reads item descriptions or schemas directly;
all scoring goes through the injected :class:`~contextweaver.protocols.Retriever`
(corpus fitted upstream by the pipeline).  Nodes outside the fitted corpus
fall back to id-token Jaccard so internal graph nodes (which carry only a
``label`` + ``routing_hint``) can still be scored.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from contextweaver._utils import jaccard, tokenize
from contextweaver.protocols import NavigationResult
from contextweaver.routing.trace import TraceStep

if TYPE_CHECKING:
    from contextweaver.protocols import Retriever
    from contextweaver.routing.graph import ChoiceGraph
    from contextweaver.types import SelectableItem

logger = logging.getLogger("contextweaver.routing")


class BeamSearchNavigator:
    """Default :class:`~contextweaver.protocols.Navigator` implementation.

    Walks a :class:`~contextweaver.routing.graph.ChoiceGraph` with bounded
    beam search.  ``beam_width`` and ``max_depth`` cap fan-out; an adaptive
    ``confidence_gap`` keeps one extra beam slot when the rank-1 vs rank-2
    spread is below the threshold (issue #14 carry-over).

    Args:
        beam_width: Beams kept at each level (default 2).
        max_depth: Maximum tree depth (default 8).
        top_k: Soft target for the backtracking pass — when fewer than
            ``top_k`` items have been collected, navigator backtracks into
            unexplored branches.  The pipeline does the final trim.
        confidence_gap: Score gap below which the navigator keeps one extra
            child per node (adaptive beam, issue #14).
    """

    def __init__(
        self,
        *,
        beam_width: int = 2,
        max_depth: int = 8,
        top_k: int = 10,
        confidence_gap: float = 0.15,
    ) -> None:
        self._beam_width = beam_width
        self._max_depth = max_depth
        self._top_k = top_k
        self._confidence_gap = confidence_gap

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def navigate(
        self,
        query: str,
        graph: ChoiceGraph,
        active_items: dict[str, SelectableItem],
        scorer: Retriever,
        doc_id_to_idx: dict[str, int],
        *,
        all_item_ids: set[str] | None = None,
        debug: bool = False,
    ) -> NavigationResult:
        """Run beam search and return collected ``(item_id, score, path)`` tuples.

        Args:
            query: Augmented scoring query.
            graph: Choice graph to walk.
            active_items: Post-filter catalog (only IDs in this dict are
                collectable).
            scorer: Fitted :class:`Retriever` used to score nodes.
            doc_id_to_idx: doc-id → corpus-index map for *scorer*.
            all_item_ids: Full pre-filter set of catalog item IDs.  Used
                to distinguish leaves (catalog items, even when filtered)
                from internal graph nodes — preserves the issue #112 /
                #22 pre-filter behaviour exactly.  Defaults to
                ``set(active_items)`` when not supplied.
            debug: When ``True``, populate ``NavigationResult.steps``.
        """
        item_ids = all_item_ids if all_item_ids is not None else set(active_items)
        eligible_internals = self._eligible_internals(graph, active_items)
        steps, collected = self._beam_search(
            query,
            graph,
            active_items,
            item_ids,
            eligible_internals,
            scorer,
            doc_id_to_idx,
            debug,
        )
        return NavigationResult(collected=collected, steps=steps)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _eligible_internals(
        self,
        graph: ChoiceGraph,
        active_items: dict[str, SelectableItem],
    ) -> set[str]:
        """Return internal nodes that have at least one descendant in *active_items*.

        Computed via reverse BFS from each active item.  Used to skip
        subtrees whose every leaf was filtered out (exclude_* / allowed_*)
        before scoring — preserves issue #112 / #22 behaviour.
        """
        eligible: set[str] = set()
        queue: list[str] = list(active_items)
        while queue:
            node = queue.pop()
            for parent in graph.predecessors(node):
                if parent in eligible:
                    continue
                eligible.add(parent)
                queue.append(parent)
        return eligible

    def _is_eligible_child(
        self,
        child: str,
        active_items: dict[str, SelectableItem],
        item_ids: set[str],
        eligible_internals: set[str],
    ) -> bool:
        """Return ``True`` when *child* may participate in beam search.

        Leaves (catalog items, regardless of post-filter state) must be in
        *active_items*; internals must reach an active descendant.
        Children failing this gate are skipped before scoring so they
        cannot consume beam slots.
        """
        if child in item_ids:
            return child in active_items
        return child in eligible_internals

    def _score_node(
        self,
        query: str,
        node_id: str,
        scorer: Retriever,
        doc_id_to_idx: dict[str, int],
    ) -> float:
        """Score a single node — fitted-corpus path then id-token Jaccard fallback."""
        idx = doc_id_to_idx.get(node_id)
        if idx is not None:
            return scorer.score_one(query, idx)
        q_tokens = tokenize(query)
        n_tokens = tokenize(node_id)
        return jaccard(q_tokens, n_tokens)

    def _beam_search(
        self,
        query: str,
        graph: ChoiceGraph,
        active_items: dict[str, SelectableItem],
        item_ids: set[str],
        eligible_internals: set[str],
        scorer: Retriever,
        doc_id_to_idx: dict[str, int],
        debug: bool,
    ) -> tuple[list[TraceStep], dict[str, tuple[float, list[str]]]]:
        """Run beam search and return ``(trace_steps, collected)``."""
        root = graph.root_id
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
                children = graph.successors(node_id)
                if not children:
                    if node_id in active_items and node_id not in collected:
                        collected[node_id] = (score, path[1:])
                    continue

                scored: list[tuple[float, str]] = []
                for child in children:
                    if not self._is_eligible_child(
                        child, active_items, item_ids, eligible_internals
                    ):
                        continue
                    s = self._score_node(query, child, scorer, doc_id_to_idx)
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
                        if child in item_ids:
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
            kept_beam = next_beam[: self._beam_width]
            if logger.isEnabledFor(logging.DEBUG):
                pruned = next_beam[self._beam_width :]
                logger.debug(
                    "navigator.beam: depth=%d, expanded=%d, kept=%d, pruned=%d, pruned_ids=%s",
                    depth,
                    len(next_beam),
                    len(kept_beam),
                    len(pruned),
                    [path[-1] for _, path in pruned],
                )
            beam = kept_beam

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
                    graph,
                    u_node,
                    u_score,
                    u_path,
                    active_items,
                    item_ids,
                    eligible_internals,
                    scorer,
                    doc_id_to_idx,
                    max_depth=remaining,
                )
                for sid, (ss, sp) in sub.items():
                    if sid in active_items and sid not in collected:
                        collected[sid] = (ss, sp)

        return steps, collected

    def _expand_subtree(
        self,
        query: str,
        graph: ChoiceGraph,
        node_id: str,
        base_score: float,
        base_path: list[str],
        active_items: dict[str, SelectableItem],
        item_ids: set[str],
        eligible_internals: set[str],
        scorer: Retriever,
        doc_id_to_idx: dict[str, int],
        *,
        max_depth: int | None = None,
    ) -> dict[str, tuple[float, list[str]]]:
        """Expand children of *node_id* recursively, collecting items."""
        depth_limit = max_depth if max_depth is not None else self._max_depth
        result: dict[str, tuple[float, list[str]]] = {}
        stack: list[tuple[float, str, list[str], int]] = [(base_score, node_id, base_path, 0)]
        while stack:
            score, nid, path, depth = stack.pop()
            children = graph.successors(nid)
            if not children or depth >= depth_limit:
                if nid in active_items:
                    result[nid] = (score, path[1:])
                continue
            for child in sorted(children):
                if not self._is_eligible_child(child, active_items, item_ids, eligible_internals):
                    continue
                s = self._score_node(query, child, scorer, doc_id_to_idx)
                new_path = path + [child]
                if child in item_ids:
                    result[child] = (score + s, new_path[1:])
                else:
                    stack.append((score + s, child, new_path, depth + 1))
        return result


def rank_collected(
    collected: dict[str, tuple[float, list[str]]],
    active_items: dict[str, SelectableItem],
) -> list[tuple[str, tuple[float, list[str]]]]:
    """Sort *collected* by ``(-score, id)`` and drop items outside *active_items*.

    Truncation to ``top_k`` is the caller's responsibility so ambiguity /
    runner-up reads can use the full ranking even when ``top_k=1`` (issue #14).
    """
    return sorted(
        (entry for entry in collected.items() if entry[0] in active_items),
        key=lambda x: (-x[1][0], x[0]),
    )

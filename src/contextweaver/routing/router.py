"""Beam-search router for contextweaver.

Performs bounded beam search over a ChoiceGraph to find the top-k items
that best match a user query.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from contextweaver._utils import TfIdfScorer, tokenize
from contextweaver.exceptions import RouteError
from contextweaver.routing.graph import ChoiceGraph
from contextweaver.types import SelectableItem


@dataclass
class RouteResult:
    """Result of a routing operation."""

    candidate_items: list[SelectableItem] = field(default_factory=list)
    candidate_ids: list[str] = field(default_factory=list)
    paths: list[list[str]] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    debug_trace: list[dict[str, Any]] | None = None


class Router:
    """Beam-search router over a ChoiceGraph."""

    def __init__(
        self,
        graph: ChoiceGraph,
        scorer: TfIdfScorer | None = None,
        beam_width: int = 2,
        max_depth: int = 8,
        top_k: int = 20,
        confidence_gap: float = 0.15,
    ) -> None:
        if not (0.0 <= confidence_gap <= 1.0):
            raise ValueError(f"confidence_gap must be in [0.0, 1.0], got {confidence_gap}")
        self._graph = graph
        self._beam_width = beam_width
        self._max_depth = max_depth
        self._top_k = top_k
        self._confidence_gap = confidence_gap

        # Auto-fit scorer on graph items
        if scorer is not None:
            self._scorer = scorer
        else:
            self._scorer = TfIdfScorer()
            items = list(graph.items.values())
            if items:
                docs = [
                    f"{it.name} {it.description} {' '.join(it.tags)}"
                    for it in sorted(items, key=lambda x: x.id)
                ]
                self._scorer.fit(docs)
                self._item_ids = [it.id for it in sorted(items, key=lambda x: x.id)]
                self._item_id_to_idx = {iid: i for i, iid in enumerate(self._item_ids)}
            else:
                self._item_ids = []
                self._item_id_to_idx = {}

    def _score_child(self, query: str, child_id: str) -> float:
        """Score a child (node or item) against query."""
        graph = self._graph
        # If it's a node, score on label + routing_hint
        if child_id in graph.nodes:
            node = graph.nodes[child_id]
            text = f"{node.label} {node.routing_hint}"
            q_tokens = tokenize(query)
            n_tokens = tokenize(text)
            if not q_tokens or not n_tokens:
                return 0.0
            intersection = q_tokens & n_tokens
            return len(intersection) / len(q_tokens | n_tokens)

        # If it's an item, use TF-IDF
        idx = self._item_id_to_idx.get(child_id)
        if idx is not None:
            return self._scorer.score(query, idx)

        return 0.0

    def route(self, query: str, *, debug: bool = False) -> RouteResult:
        """Beam search + backtracking."""
        graph = self._graph
        if not graph.nodes:
            raise RouteError("Graph has no nodes")
        if graph.root_id not in graph.nodes:
            raise RouteError(f"Root node {graph.root_id!r} not found")

        trace: list[dict[str, Any]] = [] if debug else []
        collected_items: dict[str, float] = {}
        collected_paths: dict[str, list[str]] = {}

        # Beam: list of (cumulative_score, path, node_id)
        beam: list[tuple[float, list[str], str]] = [(0.0, [graph.root_id], graph.root_id)]

        for depth in range(self._max_depth):
            if not beam:
                break

            next_beam: list[tuple[float, list[str], str]] = []

            for cum_score, path, node_id in beam:
                if node_id not in graph.nodes:
                    continue
                node = graph.nodes[node_id]
                if not node.children:
                    continue

                # Score all children
                scored_children: list[tuple[float, str]] = []
                for child_id in node.children:
                    s = self._score_child(query, child_id)
                    scored_children.append((s, child_id))

                # Sort desc by score, ties alphabetical by id
                scored_children.sort(key=lambda x: (-x[0], x[1]))

                if debug:
                    trace.append(
                        {
                            "depth": depth,
                            "node": node_id,
                            "children_scored": [
                                {"id": cid, "score": round(s, 4)} for s, cid in scored_children
                            ],
                        }
                    )

                # Determine beam width for this expansion
                bw = self._beam_width
                if (
                    len(scored_children) >= 2
                    and scored_children[0][0] - scored_children[1][0] < self._confidence_gap
                ):
                    bw = self._beam_width + 1

                for s, child_id in scored_children[:bw]:
                    new_score = cum_score + s
                    new_path = path + [child_id]
                    ct = node.child_types.get(child_id, "item")

                    if ct == "node":
                        next_beam.append((new_score, new_path, child_id))
                    else:
                        # Leaf item
                        if child_id not in collected_items or new_score > collected_items[child_id]:
                            collected_items[child_id] = new_score
                            collected_paths[child_id] = new_path

            # Sort next_beam and keep top beam_width
            next_beam.sort(key=lambda x: (-x[0], x[1]))
            beam = next_beam[: self._beam_width]

        # Backtrack: if we have fewer candidates than beam_width, expand
        # (simplified: we just collect what we have)

        # Deduplicate, sort by score desc (ties: alphabetical), return top_k
        sorted_items = sorted(collected_items.items(), key=lambda x: (-x[1], x[0]))
        top = sorted_items[: self._top_k]

        result = RouteResult(
            candidate_items=[graph.items[iid] for iid, _ in top if iid in graph.items],
            candidate_ids=[iid for iid, _ in top],
            paths=[collected_paths.get(iid, []) for iid, _ in top],
            scores={iid: score for iid, score in top},
            debug_trace=trace if debug else None,
        )
        return result

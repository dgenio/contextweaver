"""Beam-search router for contextweaver.

Performs a bounded beam search over a :class:`~contextweaver.routing.graph.ChoiceGraph`
to find the top-k items that best satisfy a user query.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from contextweaver._utils import TfIdfScorer, jaccard, tokenize
from contextweaver.exceptions import RouteError
from contextweaver.routing.graph import ChoiceGraph
from contextweaver.types import SelectableItem

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
        debug_trace: Step-by-step trace; only populated when ``debug=True``.
    """

    candidate_items: list[SelectableItem] = field(default_factory=list)
    candidate_ids: list[str] = field(default_factory=list)
    paths: list[list[str]] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)
    debug_trace: list[dict[str, Any]] = field(default_factory=list)


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
            Equivalent to calling :meth:`set_items` after construction.
        scorer: Optional pre-fitted :class:`TfIdfScorer`.  If ``None``,
            one is auto-fitted on the graph's item text representations.
        beam_width: Number of beams to keep at each level (default 2).
        max_depth: Maximum tree depth to traverse (default 8).
        top_k: Maximum number of results to return (default 20).
        confidence_gap: Minimum score gap between rank-1 and rank-2 to
            consider the top pick confident.  Must be in ``[0.0, 1.0]``.

    Raises:
        ValueError: If *confidence_gap* is outside ``[0.0, 1.0]``.
    """

    def __init__(
        self,
        graph: ChoiceGraph,
        items: list[SelectableItem] | None = None,
        scorer: TfIdfScorer | None = None,
        beam_width: int = 2,
        max_depth: int = 8,
        top_k: int = 20,
        confidence_gap: float = 0.15,
    ) -> None:
        if not 0.0 <= confidence_gap <= 1.0:
            raise ValueError(
                f"confidence_gap must be in [0.0, 1.0], got {confidence_gap}"
            )
        self._graph = graph
        self._beam_width = beam_width
        self._max_depth = max_depth
        self._top_k = top_k
        self._confidence_gap = confidence_gap
        self._items: dict[str, SelectableItem] = {}
        self._scorer = scorer
        self._indexed = False
        if items is not None:
            self.set_items(items)

    def set_items(self, items: list[SelectableItem]) -> None:
        """Register the catalog items for TF-IDF indexing and result lookup.

        Args:
            items: All items in the catalog.
        """
        self._items = {it.id: it for it in items}
        self._indexed = False

    def _ensure_index(self) -> None:
        """Lazily fit the TF-IDF scorer on item + node texts."""
        if self._indexed and self._scorer is not None:
            return
        if self._scorer is None:
            self._scorer = TfIdfScorer()

        # Build document corpus: items first (by sorted id), then non-leaf nodes
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
        self._doc_id_to_idx: dict[str, int] = {
            did: i for i, did in enumerate(doc_ids)
        }
        self._indexed = True

    def _score_node(self, query: str, node_id: str) -> float:
        """Score a single graph node against *query*."""
        self._ensure_index()
        assert self._scorer is not None

        idx = self._doc_id_to_idx.get(node_id)
        if idx is not None:
            return self._scorer.score(query, idx)

        # Fallback to Jaccard for nodes not in the index
        q_tokens = tokenize(query)
        n_tokens = tokenize(node_id.replace(":", " ").replace("_", " ").replace("/", " "))
        return jaccard(q_tokens, n_tokens)

    def route(self, query: str, *, debug: bool = False) -> RouteResult:
        """Route *query* through the graph and return ranked results.

        Algorithm:
        1. Start at root.
        2. At each node: TF-IDF score children.  Sort descending (ties:
           alphabetical by id).  Keep top *beam_width*.
        3. If >= 2 candidates scored and gap between rank-1 and rank-2 is
           less than *confidence_gap*: keep *beam_width* + 1.
        4. Recurse into node-type children (up to *max_depth*).  Collect
           leaf items.
        5. Backtrack if candidates < beam_width: expand next-best unexplored
           branch.
        6. Deduplicate, sort by score desc (ties: alphabetical), return
           top *top_k*.

        Args:
            query: The user query string.
            debug: If True, populate ``RouteResult.debug_trace``.

        Returns:
            A :class:`RouteResult` with ranked items.

        Raises:
            RouteError: If the graph is empty or invalid, or if no items
                have been registered via *items* or :meth:`set_items`.
        """
        if not self._items:
            raise RouteError(
                "No items registered. Pass items to Router() or call"
                " set_items() before routing."
            )
        root = self._graph.root_id
        if root not in self._graph.nodes():
            raise RouteError(f"Root node {root!r} not in graph.")

        self._ensure_index()
        trace: list[dict[str, Any]] = []

        # Beam entry: (score, path)
        beam: list[tuple[float, list[str]]] = [(0.0, [root])]
        collected: dict[str, tuple[float, list[str]]] = {}
        # Track unexplored branches for backtracking
        unexplored: list[tuple[float, str, list[str]]] = []

        for depth in range(self._max_depth):
            if not beam:
                break

            next_beam: list[tuple[float, list[str]]] = []
            step_trace: dict[str, Any] = {"depth": depth, "expansions": []}

            for score, path in beam:
                node_id = path[-1]
                children = self._graph.successors(node_id)

                if not children:
                    # Leaf node — collect if it's an item
                    if node_id in self._items and node_id not in collected:
                        collected[node_id] = (score, path[1:])
                    continue

                # Score all children
                scored: list[tuple[float, str]] = []
                for child in children:
                    s = self._score_node(query, child)
                    scored.append((s, child))

                # Sort: descending score, alphabetical id for ties
                scored.sort(key=lambda x: (-x[0], x[1]))

                if debug:
                    step_trace["expansions"].append({
                        "node": node_id,
                        "scored_children": [
                            {"id": cid, "score": round(cs, 4)}
                            for cs, cid in scored
                        ],
                    })

                # Determine beam width for this expansion
                keep = self._beam_width
                if (
                    len(scored) >= 2
                    and scored[0][0] - scored[1][0] < self._confidence_gap
                ):
                    keep = self._beam_width + 1

                # Keep top-k, stash rest for backtracking
                for i, (s, child) in enumerate(scored):
                    new_path = path + [child]
                    if i < keep:
                        # Check if child is an item (leaf)
                        if child in self._items:
                            if child not in collected:
                                collected[child] = (score + s, new_path[1:])
                        else:
                            next_beam.append((score + s, new_path))
                    else:
                        unexplored.append((score + s, child, new_path))

            # Sort next beam deterministically: score desc, node ID alpha
            next_beam.sort(key=lambda x: (-x[0], x[1][-1]))
            beam = next_beam[:self._beam_width]

            if debug:
                trace.append(step_trace)

        # Collect any remaining beam items
        for score, path in beam:
            node_id = path[-1]
            if node_id in self._items and node_id not in collected:
                collected[node_id] = (score, path[1:])

        # Backtrack: if we have fewer candidates than top_k,
        # expand next-best unexplored branches
        unexplored.sort(key=lambda x: (-x[0], x[1]))
        while len(collected) < self._top_k and unexplored:
            u_score, u_node, u_path = unexplored.pop(0)
            if u_node in collected:
                continue
            if u_node in self._items:
                collected[u_node] = (u_score, u_path[1:])
            else:
                # Expand this node's subtree
                sub_items = self._expand_subtree(query, u_node, u_score, u_path)
                for sid, (ss, sp) in sub_items.items():
                    if sid not in collected:
                        collected[sid] = (ss, sp)

        # Build result: sort by score desc, then alphabetical id
        ranked = sorted(
            collected.items(),
            key=lambda x: (-x[1][0], x[0]),
        )
        ranked = ranked[:self._top_k]

        result = RouteResult(
            candidate_items=[
                self._items[item_id]
                for item_id, _ in ranked
                if item_id in self._items
            ],
            candidate_ids=[item_id for item_id, _ in ranked],
            paths=[path for _, (_, path) in ranked],
            scores=[score for _, (score, _) in ranked],
        )
        if debug:
            result.debug_trace = trace

        return result

    def _expand_subtree(
        self,
        query: str,
        node_id: str,
        base_score: float,
        base_path: list[str],
    ) -> dict[str, tuple[float, list[str]]]:
        """Expand all children of *node_id* recursively, collecting items."""
        result: dict[str, tuple[float, list[str]]] = {}
        stack: list[tuple[float, str, list[str]]] = [
            (base_score, node_id, base_path)
        ]
        while stack:
            score, nid, path = stack.pop()
            children = self._graph.successors(nid)
            if not children:
                if nid in self._items:
                    result[nid] = (score, path[1:])
                continue
            for child in sorted(children):
                s = self._score_node(query, child)
                new_path = path + [child]
                if child in self._items:
                    result[child] = (score + s, new_path[1:])
                else:
                    stack.append((score + s, child, new_path))
        return result

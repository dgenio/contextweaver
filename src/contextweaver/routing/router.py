"""Beam-search router for contextweaver.

Performs a bounded beam search over a :class:`~contextweaver.routing.graph.ChoiceGraph`
to find the top-k paths that best satisfy a user query.
"""

from __future__ import annotations

from contextweaver._utils import TfIdfScorer, jaccard, tokenize
from contextweaver.exceptions import ItemNotFoundError, RouteError
from contextweaver.routing.catalog import Catalog
from contextweaver.routing.graph import ChoiceGraph


class Router:
    """Beam-search router over a :class:`~contextweaver.routing.graph.ChoiceGraph`.

    The router scores each candidate node using TF-IDF similarity between the
    query and the description of the corresponding item in the catalog.
    Nodes not present in the catalog (e.g. namespace / category nodes) receive
    a zero relevance score but are still traversed.

    Determinism guarantee: ties are broken by node ID (lexicographic).
    """

    def __init__(
        self,
        catalog: Catalog,
        beam_width: int = 5,
        max_depth: int = 10,
    ) -> None:
        self._catalog = catalog
        self._beam_width = beam_width
        self._max_depth = max_depth
        self._scorer = TfIdfScorer()
        self._indexed = False

    def _ensure_index(self) -> None:
        if not self._indexed:
            items = self._catalog.all()
            docs = [f"{item.name} {item.description} {' '.join(item.tags)}" for item in items]
            self._scorer.fit(docs)
            self._item_ids = [item.id for item in items]
            self._indexed = True

    def _score_node(self, query: str, node_id: str) -> float:
        """Score a single graph node against *query*.

        Catalog items are scored using TF-IDF against the pre-built index.
        Namespace / category nodes (not in the catalog) fall back to Jaccard
        token overlap on the node ID.
        """
        self._ensure_index()
        # Try catalog lookup first
        try:
            item = self._catalog.get(node_id)
        except ItemNotFoundError:
            # Namespace / category nodes — fall back to token overlap
            q_tokens = tokenize(query)
            n_tokens = tokenize(node_id.replace(":", " ").replace("_", " "))
            return jaccard(q_tokens, n_tokens)

        # Use TF-IDF scoring for catalog items
        if node_id in self._item_ids:
            idx = self._item_ids.index(node_id)
            return self._scorer.score(query, idx)

        # Fallback to Jaccard if item is somehow not in the index
        doc_text = f"{item.name} {item.description} {' '.join(item.tags)}"
        q_tokens = tokenize(query)
        d_tokens = tokenize(doc_text)
        return jaccard(q_tokens, d_tokens)

    def route(self, query: str, graph: ChoiceGraph, start: str = "root") -> list[list[str]]:
        """Return up to *beam_width* paths through *graph* ranked by relevance.

        Each path is a list of node IDs from *start* (exclusive) to a leaf.

        Args:
            query: The user query to route.
            graph: The choice graph to search.
            start: Starting node ID (default: ``"root"``).

        Returns:
            A list of paths (each a ``list[str]``), best first.

        Raises:
            RouteError: If *start* is not a node in *graph*.
        """
        if start not in graph.nodes():
            raise RouteError(f"Start node {start!r} not in graph.")

        # Each beam entry: (score, path_so_far)
        beam: list[tuple[float, list[str]]] = [(0.0, [start])]
        completed: list[tuple[float, list[str]]] = []

        for _ in range(self._max_depth):
            if not beam:
                break
            candidates: list[tuple[float, list[str]]] = []
            for score, path in beam:
                node = path[-1]
                succs = graph.successors(node)
                if not succs:
                    completed.append((score, path[1:]))  # strip root from output
                    continue
                for succ in succs:
                    s = self._score_node(query, succ)
                    candidates.append((score + s, path + [succ]))

            # Sort: descending score, then lexicographic path for determinism
            candidates.sort(key=lambda x: (-x[0], x[1]))
            beam = candidates[: self._beam_width]

        # Collect any paths still in beam as completed
        for score, path in beam:
            completed.append((score, path[1:]))  # strip root

        if not completed:
            raise RouteError("No routes found through the graph.")

        completed.sort(key=lambda x: (-x[0], x[1]))
        return [path for _, path in completed[: self._beam_width]]

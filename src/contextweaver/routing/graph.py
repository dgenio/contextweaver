"""Choice graph (routing DAG) for the contextweaver Routing Engine.

The :class:`ChoiceGraph` is a directed acyclic graph where nodes are
:class:`~contextweaver.types.SelectableItem` IDs and edges encode feasibility
relationships (e.g. "B can only be called after A").  The router performs
beam search over this graph.
"""

from __future__ import annotations

from typing import Any

from contextweaver.exceptions import GraphBuildError

# FUTURE: DAG mode with conditional edges and weighted constraints.


class ChoiceGraph:
    """Bounded DAG of selectable-item IDs.

    Nodes are string IDs.  Directed edges go from *prerequisite* to
    *dependent* (``A → B`` means "A must come before B").

    The graph is validated on mutation: cycles are detected eagerly and raise
    :class:`~contextweaver.exceptions.GraphBuildError`.
    """

    def __init__(self) -> None:
        self._nodes: set[str] = set()
        self._edges: dict[str, set[str]] = {}  # source → {targets}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_node(self, node_id: str) -> None:
        """Register a node without any edges.

        Args:
            node_id: The item ID to add.
        """
        self._nodes.add(node_id)
        if node_id not in self._edges:
            self._edges[node_id] = set()

    def add_edge(self, src: str, dst: str) -> None:
        """Add a directed edge *src* → *dst* and validate acyclicity.

        Both *src* and *dst* are automatically added as nodes if not present.
        Cycle detection is incremental: after adding the edge, only the
        reachability from *dst* back to *src* is checked (rather than a
        full-graph DFS), so each call is O(reachable-from-dst) instead of
        O(V + E).

        Args:
            src: Source node ID (prerequisite).
            dst: Destination node ID (dependent).

        Raises:
            GraphBuildError: If adding this edge would create a cycle.
        """
        self.add_node(src)
        self.add_node(dst)
        self._edges[src].add(dst)
        if self._creates_cycle(src, dst):
            self._edges[src].discard(dst)
            raise GraphBuildError(f"Adding edge {src!r} → {dst!r} would create a cycle.")

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def nodes(self) -> list[str]:
        """Return all node IDs in sorted order."""
        return sorted(self._nodes)

    def successors(self, node_id: str) -> list[str]:
        """Return the direct successors of *node_id* in sorted order.

        Args:
            node_id: The source node.

        Returns:
            A sorted list of destination node IDs.
        """
        return sorted(self._edges.get(node_id, set()))

    def predecessors(self, node_id: str) -> list[str]:
        """Return the direct predecessors of *node_id* in sorted order.

        Args:
            node_id: The destination node.

        Returns:
            A sorted list of source node IDs.
        """
        return sorted(src for src, dsts in self._edges.items() if node_id in dsts)

    def roots(self) -> list[str]:
        """Return nodes with no incoming edges (sorted)."""
        all_dsts: set[str] = {dst for dsts in self._edges.values() for dst in dsts}
        return sorted(n for n in self._nodes if n not in all_dsts)

    def topological_order(self) -> list[str]:
        """Return a valid topological ordering of all nodes.

        Returns:
            A list of node IDs in topological order (sources before dependents).

        Raises:
            GraphBuildError: If the graph contains a cycle (should not happen
                if edges were added via :meth:`add_edge`).
        """
        in_degree: dict[str, int] = {n: 0 for n in self._nodes}
        for dsts in self._edges.values():
            for dst in dsts:
                in_degree[dst] += 1
        queue = sorted(n for n, d in in_degree.items() if d == 0)
        order: list[str] = []
        while queue:
            node = queue.pop(0)
            order.append(node)
            for dst in sorted(self._edges.get(node, set())):
                in_degree[dst] -= 1
                if in_degree[dst] == 0:
                    queue.append(dst)
                    queue.sort()
        if len(order) != len(self._nodes):
            raise GraphBuildError("Cycle detected during topological sort.")
        return order

    # ------------------------------------------------------------------
    # Cycle detection
    # ------------------------------------------------------------------

    def _creates_cycle(self, src: str, dst: str) -> bool:
        """Return True if *dst* can reach *src* (i.e. the new edge closes a cycle).

        Only traverses the subgraph reachable from *dst*, which is cheaper
        than a full-graph DFS when the graph is large and sparsely connected.
        """
        # Self-loop is the trivial case.
        if src == dst:
            return True
        visited: set[str] = set()
        stack = [dst]
        while stack:
            node = stack.pop()
            if node == src:
                return True
            if node in visited:
                continue
            visited.add(node)
            stack.extend(self._edges.get(node, set()))
        return False

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "nodes": sorted(self._nodes),
            "edges": {src: sorted(dsts) for src, dsts in sorted(self._edges.items())},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChoiceGraph:
        """Deserialise from a JSON-compatible dict produced by :meth:`to_dict`.

        Raises:
            GraphBuildError: If the serialised data contains a cycle.
        """
        graph = cls()
        for node in data.get("nodes", []):
            graph.add_node(node)
        for src, dsts in data.get("edges", {}).items():
            for dst in dsts:
                graph.add_edge(src, dst)
        return graph

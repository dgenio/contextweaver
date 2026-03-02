"""Choice graph (routing DAG) for the contextweaver Routing Engine.

The :class:`ChoiceGraph` is a bounded DAG where each node is a
:class:`ChoiceNode` (either a navigation node or a leaf item).  The router
performs beam search over this graph.

Nodes distinguish between child *nodes* (which can be expanded further)
and child *items* (leaf-level catalog entries) via :attr:`ChoiceNode.child_types`.

.. todo::
   This module exceeds the ~300-line target (~490 lines).  Consider
   extracting ChoiceNode, serialisation/IO, and stats/validation into
   separate sub-modules in a follow-up refactoring PR.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from contextweaver.exceptions import GraphBuildError

# ---------------------------------------------------------------------------
# ChoiceNode
# ---------------------------------------------------------------------------


@dataclass
class ChoiceNode:
    """A single node in the routing :class:`ChoiceGraph`.

    Attributes:
        node_id: Unique identifier for this node.
        label: Short human-readable label shown during routing.
        routing_hint: A sentence describing what this group of children is about.
        children: Ordered list of child IDs (both nodes and items).
        child_types: Mapping of child ID to ``"node"`` or ``"item"``.
        stats: Arbitrary statistics dict (populated by :meth:`ChoiceGraph.stats`).
    """

    node_id: str
    label: str = ""
    routing_hint: str = ""
    children: list[str] = field(default_factory=list)
    child_types: dict[str, str] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "node_id": self.node_id,
            "label": self.label,
            "routing_hint": self.routing_hint,
            "children": list(self.children),
            "child_types": dict(self.child_types),
            "stats": dict(self.stats),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChoiceNode:
        """Deserialise from a JSON-compatible dict."""
        return cls(
            node_id=data["node_id"],
            label=data.get("label", ""),
            routing_hint=data.get("routing_hint", ""),
            children=list(data.get("children", [])),
            child_types=dict(data.get("child_types", {})),
            stats=dict(data.get("stats", {})),
        )


# ---------------------------------------------------------------------------
# ChoiceGraph
# ---------------------------------------------------------------------------


class ChoiceGraph:
    """Bounded DAG of :class:`ChoiceNode` objects.

    Nodes carry labels and routing hints.  Directed edges go from *parent* to
    *child*.  Items (leaf entries) are stored separately.

    The graph is validated on mutation: cycles are detected eagerly and raise
    :class:`~contextweaver.exceptions.GraphBuildError`.
    """

    def __init__(self, max_children: int = 20) -> None:
        self._nodes: dict[str, ChoiceNode] = {}
        self._items: set[str] = set()
        self._edges: dict[str, set[str]] = {}
        self._max_children = max_children
        self._root_id: str = "root"
        self._build_meta: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def root_id(self) -> str:
        """Return the root node ID."""
        return self._root_id

    @root_id.setter
    def root_id(self, value: str) -> None:
        """Set the root node ID."""
        self._root_id = value

    @property
    def build_meta(self) -> dict[str, Any]:
        """Return build metadata."""
        return self._build_meta

    @build_meta.setter
    def build_meta(self, value: dict[str, Any]) -> None:
        """Set build metadata."""
        self._build_meta = value

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_node(self, node_id: str, label: str = "", routing_hint: str = "") -> None:
        """Register a node.

        Args:
            node_id: The node ID to add.
            label: Short label for the node.
            routing_hint: Human-readable routing hint.
        """
        if node_id not in self._nodes:
            self._nodes[node_id] = ChoiceNode(
                node_id=node_id, label=label, routing_hint=routing_hint
            )
        else:
            if label:
                self._nodes[node_id].label = label
            if routing_hint:
                self._nodes[node_id].routing_hint = routing_hint
        if node_id not in self._edges:
            self._edges[node_id] = set()

    def add_item(self, item_id: str) -> None:
        """Register a leaf item ID.

        Args:
            item_id: The item ID to track.
        """
        self._items.add(item_id)

    def add_edge(self, src: str, dst: str) -> None:
        """Add a directed edge *src* -> *dst* and validate acyclicity.

        Both *src* and *dst* are automatically added as nodes if not present.
        If *dst* is registered as an item (via :meth:`add_item`), it still
        receives a :class:`ChoiceNode` representation so that cycle detection
        and topological ordering work uniformly over the DAG.

        Args:
            src: Source node ID (parent).
            dst: Destination node ID (child).

        Raises:
            GraphBuildError: If adding this edge would create a cycle.
        """
        self.add_node(src)
        self.add_node(dst)
        self._edges[src].add(dst)
        if self._creates_cycle(src, dst):
            self._edges[src].discard(dst)
            raise GraphBuildError(
                f"Adding edge {src!r} -> {dst!r} would create a cycle."
            )
        # Update parent's children list
        node = self._nodes[src]
        if dst not in node.children:
            node.children.append(dst)
            child_type = "item" if dst in self._items else "node"
            node.child_types[dst] = child_type

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def nodes(self) -> list[str]:
        """Return all node IDs in sorted order."""
        return sorted(self._nodes)

    def items(self) -> list[str]:
        """Return all item IDs in sorted order."""
        return sorted(self._items)

    def get_node(self, node_id: str) -> ChoiceNode:
        """Return the :class:`ChoiceNode` for *node_id*.

        Raises:
            GraphBuildError: If the node does not exist.
        """
        if node_id not in self._nodes:
            raise GraphBuildError(f"Node {node_id!r} not found.")
        return self._nodes[node_id]

    def successors(self, node_id: str) -> list[str]:
        """Return the direct successors of *node_id* in sorted order."""
        return sorted(self._edges.get(node_id, set()))

    def predecessors(self, node_id: str) -> list[str]:
        """Return the direct predecessors of *node_id* in sorted order."""
        return sorted(src for src, dsts in self._edges.items() if node_id in dsts)

    def roots(self) -> list[str]:
        """Return nodes with no incoming edges (sorted)."""
        all_dsts: set[str] = {dst for dsts in self._edges.values() for dst in dsts}
        return sorted(n for n in self._nodes if n not in all_dsts)

    def topological_order(self) -> list[str]:
        """Return a valid topological ordering of all nodes.

        Raises:
            GraphBuildError: If the graph contains a cycle.
        """
        in_degree: dict[str, int] = {n: 0 for n in self._nodes}
        for dsts in self._edges.values():
            for dst in dsts:
                if dst in in_degree:
                    in_degree[dst] += 1
        queue = sorted(n for n, d in in_degree.items() if d == 0)
        order: list[str] = []
        while queue:
            node = queue.pop(0)
            order.append(node)
            for dst in sorted(self._edges.get(node, set())):
                if dst in in_degree:
                    in_degree[dst] -= 1
                    if in_degree[dst] == 0:
                        queue.append(dst)
                        queue.sort()
        if len(order) != len(self._nodes):
            raise GraphBuildError("Cycle detected during topological sort.")
        return order

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Compute graph statistics.

        ``total_nodes`` and ``leaf_node_count`` count only *navigation*
        nodes (i.e. they exclude IDs registered via :meth:`add_item`).

        Returns:
            A dict with keys: ``total_items``, ``total_nodes``, ``max_depth``,
            ``avg_branching_factor``, ``max_branching_factor``,
            ``leaf_node_count``, ``item_kinds``, ``namespaces``.
        """
        # Exclude item IDs from node counts so total_nodes reflects
        # navigation nodes only (items are counted in total_items).
        nav_nodes = [n for n in self._nodes if n not in self._items]
        total_nodes = len(nav_nodes)
        total_items = len(self._items)

        # Branching factors (navigation nodes only)
        branching: list[int] = []
        leaf_count = 0
        for node_id in nav_nodes:
            children_count = len(self._edges.get(node_id, set()))
            branching.append(children_count)
            if children_count == 0:
                leaf_count += 1

        avg_bf = sum(branching) / len(branching) if branching else 0.0
        max_bf = max(branching) if branching else 0

        # Max depth via BFS from root
        max_depth = 0
        if self._root_id in self._nodes:
            queue_d: deque[tuple[str, int]] = deque([(self._root_id, 0)])
            visited: set[str] = set()
            while queue_d:
                nid, depth = queue_d.popleft()
                if nid in visited:
                    continue
                visited.add(nid)
                max_depth = max(max_depth, depth)
                for child in self._edges.get(nid, set()):
                    if child in self._nodes and child not in visited:
                        queue_d.append((child, depth + 1))

        # Collect namespaces from item IDs
        namespaces: set[str] = set()
        for item_id in self._items:
            parts = item_id.split(".")
            if len(parts) >= 2:
                namespaces.add(parts[0])

        return {
            "total_items": total_items,
            "total_nodes": total_nodes,
            "max_depth": max_depth,
            "avg_branching_factor": round(avg_bf, 2),
            "max_branching_factor": max_bf,
            "leaf_node_count": leaf_count,
            "item_kinds": [],
            "namespaces": sorted(namespaces),
        }

    # ------------------------------------------------------------------
    # Cycle detection
    # ------------------------------------------------------------------

    def _creates_cycle(self, src: str, dst: str) -> bool:
        """Return True if *dst* can reach *src* (i.e. the new edge closes a cycle)."""
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
            "root_id": self._root_id,
            "nodes": {
                nid: node.to_dict()
                for nid, node in sorted(self._nodes.items())
            },
            "items": sorted(self._items),
            "edges": {
                src: sorted(dsts)
                for src, dsts in sorted(self._edges.items())
            },
            "max_children": self._max_children,
            "build_meta": dict(self._build_meta),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChoiceGraph:
        """Deserialise from a JSON-compatible dict produced by :meth:`to_dict`.

        Raises:
            GraphBuildError: If the serialised data contains a cycle.
        """
        graph = cls(max_children=data.get("max_children", 20))
        graph._root_id = data.get("root_id", "root")
        graph._build_meta = dict(data.get("build_meta", {}))

        # Restore items first so add_edge can classify children
        for item_id in data.get("items", []):
            graph.add_item(item_id)

        # Restore nodes with metadata
        for nid, node_data in data.get("nodes", {}).items():
            node = ChoiceNode.from_dict(node_data)
            graph._nodes[nid] = node
            if nid not in graph._edges:
                graph._edges[nid] = set()

        # Restore edges (with cycle checking)
        for src, dsts in data.get("edges", {}).items():
            for dst in dsts:
                if src not in graph._nodes:
                    graph.add_node(src)
                if dst not in graph._nodes:
                    graph.add_node(dst)
                graph._edges.setdefault(src, set()).add(dst)
                if graph._creates_cycle(src, dst):
                    graph._edges[src].discard(dst)
                    raise GraphBuildError(
                        f"Cycle detected loading edge {src!r} -> {dst!r}."
                    )

        # Rebuild children / child_types from _edges so they are
        # always consistent, regardless of what the serialised node
        # metadata contained.
        for node in graph._nodes.values():
            node.children.clear()
            node.child_types.clear()
        for src, dsts in graph._edges.items():
            if src not in graph._nodes:
                continue
            node = graph._nodes[src]
            for dst in sorted(dsts):
                node.children.append(dst)
                node.child_types[dst] = (
                    "item" if dst in graph._items else "node"
                )

        return graph

    # ------------------------------------------------------------------
    # File I/O
    # ------------------------------------------------------------------

    def save(self, path: str | Path) -> None:
        """Write the graph to a JSON file with deterministic formatting.

        Args:
            path: Filesystem path for the output file.
        """
        Path(path).write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> ChoiceGraph:
        """Load a graph from a JSON file and validate it.

        Validates: root_id exists, all child refs resolve, no cycles (DFS),
        all items reachable from root.

        Args:
            path: Filesystem path to a JSON file.

        Returns:
            A validated :class:`ChoiceGraph`.

        Raises:
            GraphBuildError: If the file is invalid or the graph fails validation.
        """
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise GraphBuildError(f"Cannot read graph file: {exc}") from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GraphBuildError(f"Invalid JSON in graph file: {exc}") from exc

        graph = cls.from_dict(data)
        graph._validate()
        return graph

    def _validate(self) -> None:
        """Validate graph integrity.

        Checks:
        1. root_id exists in nodes.
        2. All child references resolve to existing nodes or items.
        3. No cycles (via topological sort).
        4. All items are reachable from root.

        Raises:
            GraphBuildError: On any validation failure.
        """
        # 1. Root must exist
        if self._root_id not in self._nodes:
            raise GraphBuildError(
                f"Root node {self._root_id!r} not found in graph."
            )

        # 2. All child refs must resolve
        all_known = set(self._nodes) | self._items
        for src, dsts in self._edges.items():
            for dst in dsts:
                if dst not in all_known:
                    raise GraphBuildError(
                        f"Child ref {dst!r} from {src!r} not found in graph."
                    )

        # 3. No cycles
        self.topological_order()

        # 4. All items reachable from root
        reachable: set[str] = set()
        stack = [self._root_id]
        while stack:
            n = stack.pop()
            if n in reachable:
                continue
            reachable.add(n)
            for child in self._edges.get(n, set()):
                if child not in reachable:
                    stack.append(child)
        unreachable = self._items - reachable
        if unreachable:
            raise GraphBuildError(
                f"Items not reachable from root: {sorted(unreachable)}"
            )

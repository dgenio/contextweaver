"""Choice graph for the contextweaver Routing Engine.

ChoiceNode + ChoiceGraph: a tree structure where each node has bounded
children (either other nodes or leaf items).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from contextweaver.exceptions import GraphBuildError
from contextweaver.types import SelectableItem


@dataclass
class ChoiceNode:
    """A node in the choice graph."""

    node_id: str
    label: str
    routing_hint: str
    children: list[str] = field(default_factory=list)
    child_types: dict[str, Literal["node", "item"]] = field(default_factory=dict)
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
            label=data["label"],
            routing_hint=data["routing_hint"],
            children=list(data.get("children", [])),
            child_types=dict(data.get("child_types", {})),
            stats=dict(data.get("stats", {})),
        )


@dataclass
class ChoiceGraph:
    """Bounded tree of choice nodes and selectable items."""

    root_id: str = "root"
    nodes: dict[str, ChoiceNode] = field(default_factory=dict)
    items: dict[str, SelectableItem] = field(default_factory=dict)
    max_children: int = 20
    build_meta: dict[str, Any] = field(default_factory=dict)

    def save(self, path: str | Path) -> None:
        """Deterministic JSON: sorted keys, consistent formatting."""
        data = self.to_dict()
        with open(path, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)

    @classmethod
    def load(cls, path: str | Path) -> ChoiceGraph:
        """Validates structural integrity on load."""
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            raise GraphBuildError(f"Failed to load graph: {exc}") from exc
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "root_id": self.root_id,
            "nodes": {k: v.to_dict() for k, v in sorted(self.nodes.items())},
            "items": {k: v.to_dict() for k, v in sorted(self.items.items())},
            "max_children": self.max_children,
            "build_meta": dict(self.build_meta),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChoiceGraph:
        """Deserialise and validate."""
        root_id = data["root_id"]
        nodes = {k: ChoiceNode.from_dict(v) for k, v in data.get("nodes", {}).items()}
        items_raw = data.get("items", {})
        items = {k: SelectableItem.from_dict(v) for k, v in items_raw.items()}
        max_children = data.get("max_children", 20)
        build_meta = dict(data.get("build_meta", {}))

        graph = cls(
            root_id=root_id,
            nodes=nodes,
            items=items,
            max_children=max_children,
            build_meta=build_meta,
        )
        graph._validate()
        return graph

    def _validate(self) -> None:
        """Validate structural integrity."""
        if self.root_id not in self.nodes:
            raise GraphBuildError(f"root_id {self.root_id!r} not found in nodes")

        # Check all child refs resolve
        for nid, node in self.nodes.items():
            for child_id in node.children:
                ct = node.child_types.get(child_id, "item")
                if ct == "node" and child_id not in self.nodes:
                    raise GraphBuildError(
                        f"Node {nid!r} references missing child node {child_id!r}"
                    )
                if ct == "item" and child_id not in self.items:
                    raise GraphBuildError(
                        f"Node {nid!r} references missing child item {child_id!r}"
                    )

        # Check no cycles via DFS from root
        visited: set[str] = set()
        path: set[str] = set()

        def dfs(node_id: str) -> None:
            if node_id in path:
                raise GraphBuildError(f"Cycle detected involving {node_id!r}")
            if node_id in visited:
                return
            path.add(node_id)
            visited.add(node_id)
            if node_id in self.nodes:
                for child_id in self.nodes[node_id].children:
                    ct = self.nodes[node_id].child_types.get(child_id, "item")
                    if ct == "node":
                        dfs(child_id)
            path.discard(node_id)

        dfs(self.root_id)

    def graph_stats(self) -> dict[str, Any]:
        """Return graph statistics."""
        total_items = len(self.items)
        total_nodes = len(self.nodes)

        if total_nodes == 0:
            return {
                "total_items": 0,
                "total_nodes": 0,
                "max_depth": 0,
                "avg_branching_factor": 0.0,
                "max_branching_factor": 0,
                "leaf_node_count": 0,
                "item_kinds": {},
                "namespaces": [],
            }

        # Compute depth and branching
        def depth_of(nid: str, d: int = 0) -> int:
            if nid not in self.nodes:
                return d
            node = self.nodes[nid]
            child_nodes = [c for c in node.children if node.child_types.get(c) == "node"]
            if not child_nodes:
                return d
            return max(depth_of(c, d + 1) for c in child_nodes)

        max_depth = depth_of(self.root_id)

        branching = [len(n.children) for n in self.nodes.values()]
        avg_branching = sum(branching) / len(branching) if branching else 0.0
        max_branching = max(branching) if branching else 0

        leaf_nodes = sum(
            1
            for n in self.nodes.values()
            if all(n.child_types.get(c) == "item" for c in n.children) and n.children
        )

        item_kinds: dict[str, int] = {}
        namespaces: set[str] = set()
        for item in self.items.values():
            item_kinds[item.kind] = item_kinds.get(item.kind, 0) + 1
            if item.namespace:
                namespaces.add(item.namespace)

        return {
            "total_items": total_items,
            "total_nodes": total_nodes,
            "max_depth": max_depth,
            "avg_branching_factor": round(avg_branching, 2),
            "max_branching_factor": max_branching,
            "leaf_node_count": leaf_nodes,
            "item_kinds": item_kinds,
            "namespaces": sorted(namespaces),
        }

"""Routing tree builder for contextweaver.

Converts a flat list of SelectableItems into a bounded ChoiceGraph tree
via recursive partitioning.
"""

from __future__ import annotations

import math
from collections import defaultdict

from contextweaver._utils import jaccard, tokenize
from contextweaver.exceptions import GraphBuildError
from contextweaver.protocols import Labeler
from contextweaver.routing.graph import ChoiceGraph, ChoiceNode
from contextweaver.routing.labeler import KeywordLabeler
from contextweaver.types import SelectableItem


class TreeBuilder:
    """Build a bounded ChoiceGraph tree from a list of SelectableItems."""

    def __init__(
        self,
        max_children: int = 20,
        labeler: Labeler | None = None,
        target_group_size: int | None = None,
    ) -> None:
        self._max_children = max_children
        self._labeler = labeler or KeywordLabeler()
        self._target_group_size = target_group_size or max_children

    def build(self, items: list[SelectableItem]) -> ChoiceGraph:
        """Recursive bounded tree.

        Algorithm:
        1. If len(items) <= max_children: leaf node with items as children.
        2. Else: partition into groups and recurse.
        """
        if not items:
            raise GraphBuildError("Cannot build graph from empty item list")

        graph = ChoiceGraph(max_children=self._max_children)

        # Store all items
        for item in items:
            graph.items[item.id] = item

        # Build tree recursively
        root_node = self._build_node(items, "root", graph)
        graph.nodes[root_node.node_id] = root_node
        graph.root_id = root_node.node_id

        graph.build_meta = {
            "version": "0.1.0",
            "strategy": "recursive_bounded_tree",
            "item_count": len(items),
            "max_children": self._max_children,
            "max_depth": graph.graph_stats()["max_depth"],
        }

        return graph

    def _build_node(
        self,
        items: list[SelectableItem],
        node_id: str,
        graph: ChoiceGraph,
    ) -> ChoiceNode:
        """Build a node for the given items."""
        if len(items) <= self._max_children:
            # Leaf node: all items are direct children
            label, hint = self._labeler.label(items)
            node = ChoiceNode(
                node_id=node_id,
                label=label,
                routing_hint=hint,
                children=[item.id for item in sorted(items, key=lambda x: x.id)],
                child_types={item.id: "item" for item in items},
            )
            return node

        # Need to partition
        groups = self._partition(items)

        label, hint = self._labeler.label(items)
        node = ChoiceNode(
            node_id=node_id,
            label=label,
            routing_hint=hint,
        )

        for i, (group_label, group_items) in enumerate(groups):
            child_id = f"{node_id}.{i}" if node_id != "root" else f"g{i}"
            child_node = self._build_node(group_items, child_id, graph)
            # Override label from partition
            child_node.label = group_label
            child_node.routing_hint = f"Tools related to {group_label}"
            graph.nodes[child_node.node_id] = child_node
            node.children.append(child_id)
            node.child_types[child_id] = "node"

        return node

    def _partition(self, items: list[SelectableItem]) -> list[tuple[str, list[SelectableItem]]]:
        """Partition items into groups. Returns (label, items) pairs."""
        # Strategy a: Namespace grouping
        ns_groups = self._namespace_partition(items)
        if ns_groups and len(ns_groups) >= 2:
            return ns_groups

        # Strategy b: Clustering via farthest-first Jaccard seeding
        cluster_groups = self._cluster_partition(items)
        if cluster_groups and len(cluster_groups) >= 2:
            return cluster_groups

        # Strategy c: Alphabetical bucketing (final fallback)
        return self._alphabetical_partition(items)

    def _namespace_partition(
        self, items: list[SelectableItem]
    ) -> list[tuple[str, list[SelectableItem]]] | None:
        """Group by first unused dot-segment of namespace."""
        groups: dict[str, list[SelectableItem]] = defaultdict(list)
        no_ns_count = 0

        for item in sorted(items, key=lambda x: x.id):
            if item.namespace:
                first_seg = item.namespace.split(".")[0]
                groups[first_seg].append(item)
            else:
                no_ns_count += 1
                groups["_other"].append(item)

        if len(groups) < 2 or no_ns_count > len(items) * 0.5:
            return None

        result = []
        for ns_key in sorted(groups.keys()):
            grp = groups[ns_key]
            label = ns_key if ns_key != "_other" else "other"
            result.append((label, grp))

        return result

    def _cluster_partition(
        self, items: list[SelectableItem]
    ) -> list[tuple[str, list[SelectableItem]]] | None:
        """Clustering via farthest-first Jaccard seeding."""
        k = min(
            self._max_children,
            math.ceil(len(items) / self._target_group_size),
        )
        if k < 2:
            return None

        # Build text representations
        sorted_items = sorted(items, key=lambda x: x.id)
        texts = [f"{item.name} {item.description} {' '.join(item.tags)}" for item in sorted_items]
        token_sets = [tokenize(t) for t in texts]

        # Farthest-first seeding
        seeds = [0]
        for _ in range(k - 1):
            max_dist = -1.0
            max_idx = 0
            for j in range(len(sorted_items)):
                if j in seeds:
                    continue
                min_sim = min(jaccard(token_sets[j], token_sets[s]) for s in seeds)
                dist = 1.0 - min_sim
                if dist > max_dist:
                    max_dist = dist
                    max_idx = j
            seeds.append(max_idx)

        # Assign to nearest seed
        clusters: dict[int, list[SelectableItem]] = {s: [] for s in seeds}
        for j, item in enumerate(sorted_items):
            if j in seeds:
                clusters[j].append(item)
            else:
                best_seed = min(seeds, key=lambda s: -jaccard(token_sets[j], token_sets[s]))
                clusters[best_seed].append(item)

        # Build result with labels
        result = []
        for seed_idx in sorted(clusters.keys()):
            grp = clusters[seed_idx]
            if grp:
                grp_label, _ = self._labeler.label(grp)
                result.append((grp_label, grp))

        return result if len(result) >= 2 else None

    def _alphabetical_partition(
        self, items: list[SelectableItem]
    ) -> list[tuple[str, list[SelectableItem]]]:
        """Sort by name, split into k chunks, label by range."""
        sorted_items = sorted(items, key=lambda x: x.name)
        k = min(
            self._max_children,
            math.ceil(len(items) / self._target_group_size),
        )
        k = max(k, 2)
        chunk_size = math.ceil(len(sorted_items) / k)

        result = []
        for i in range(0, len(sorted_items), chunk_size):
            chunk = sorted_items[i : i + chunk_size]
            first = chunk[0].name[0].upper() if chunk else "?"
            last = chunk[-1].name[0].upper() if chunk else "?"
            label = f"{first}-{last}" if first != last else first
            result.append((label, chunk))

        return result

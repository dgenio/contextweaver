"""Routing tree builder for contextweaver.

Converts a flat list of :class:`~contextweaver.types.SelectableItem` objects
into a :class:`~contextweaver.routing.graph.ChoiceGraph` by grouping items
under intermediate nodes.  Three grouping strategies are tried in priority
order:

1. **Namespace grouping** — group by first unused namespace dot-segment.
2. **Clustering** — farthest-first Jaccard seeding + nearest assignment.
3. **Alphabetical fallback** — sort by name and split into labelled chunks.

The builder guarantees that every node in the resulting graph has at most
*max_children* children.  Empty input raises
:class:`~contextweaver.exceptions.GraphBuildError`.
"""

from __future__ import annotations

import math
from collections import defaultdict

from contextweaver._utils import jaccard, tokenize
from contextweaver.exceptions import GraphBuildError
from contextweaver.routing.graph import ChoiceGraph
from contextweaver.routing.labeler import KeywordLabeler
from contextweaver.types import SelectableItem


def _text_repr(item: SelectableItem) -> str:
    """Build a text representation for similarity computation."""
    return f"{item.name} {item.description} {' '.join(item.tags)}"


class TreeBuilder:
    """Build a bounded :class:`ChoiceGraph` from a list of items.

    The tree guarantees every node has at most *max_children* children.
    The builder is deterministic: identical inputs always produce identical
    graphs.

    Args:
        max_children: Maximum children per node (default 20).
        labeler: Optional :class:`KeywordLabeler` instance.
        target_group_size: Hint for cluster sizes; defaults to *max_children*.
    """

    def __init__(
        self,
        max_children: int = 20,
        labeler: KeywordLabeler | None = None,
        target_group_size: int | None = None,
    ) -> None:
        self._max_children = max_children
        self._labeler = labeler or KeywordLabeler()
        self._target_group_size = target_group_size or max_children

    def build(self, items: list[SelectableItem]) -> ChoiceGraph:
        """Build a :class:`ChoiceGraph` from *items*.

        Strategies are tried in priority order:
        1. Namespace grouping (if items have namespaces and >= 2 groups form).
        2. Clustering (Jaccard-based k-means variant).
        3. Alphabetical fallback.

        Args:
            items: The items to organise.

        Returns:
            A bounded DAG rooted at ``"root"``.

        Raises:
            GraphBuildError: If *items* is empty.
        """
        if not items:
            raise GraphBuildError("Cannot build tree from empty item list.")

        graph = ChoiceGraph(max_children=self._max_children)
        graph.add_node("root", label="root", routing_hint="All available tools")

        # Register all items in the graph
        for item in items:
            graph.add_item(item.id)

        # Sort items by id for determinism
        sorted_items = sorted(items, key=lambda it: it.id)

        self._build_subtree(graph, "root", sorted_items, depth=0)

        graph.build_meta = {
            "version": "1.0",
            "strategy": "auto",
            "item_count": len(items),
            "max_depth": graph.stats()["max_depth"],
        }

        return graph

    def _build_subtree(
        self,
        graph: ChoiceGraph,
        parent_id: str,
        items: list[SelectableItem],
        depth: int,
    ) -> None:
        """Recursively build a subtree under *parent_id*."""
        if len(items) <= self._max_children:
            # Leaf group — attach items directly
            for item in sorted(items, key=lambda it: it.id):
                graph.add_node(item.id)
                graph.add_edge(parent_id, item.id)
            return

        # Try strategies in priority order
        groups = self._try_namespace_grouping(items)
        if groups is None:
            groups = self._try_clustering(items)
        if groups is None:
            groups = self._alphabetical_fallback(items)

        # If strategy produced more groups than max_children, merge
        # adjacent groups (sorted by key) until within bounds.
        if len(groups) > self._max_children:
            groups = self._coalesce_groups(groups)

        # Create intermediate nodes and recurse
        for group_label, group_items in sorted(groups.items()):
            node_id = f"{parent_id}/{group_label}"
            label, routing_hint = self._labeler.label_group(group_items)
            graph.add_node(node_id, label=label, routing_hint=routing_hint)
            graph.add_edge(parent_id, node_id)
            self._build_subtree(graph, node_id, group_items, depth + 1)

    # ------------------------------------------------------------------
    # Strategy 1: Namespace grouping
    # ------------------------------------------------------------------

    def _try_namespace_grouping(
        self, items: list[SelectableItem]
    ) -> dict[str, list[SelectableItem]] | None:
        """Group by first unused namespace dot-segment.

        Returns None (fallback) if fewer than 2 groups form or most items
        lack namespaces.
        """
        with_ns = [it for it in items if it.namespace]
        if len(with_ns) < len(items) * 0.5:
            return None

        groups: dict[str, list[SelectableItem]] = defaultdict(list)
        for item in sorted(items, key=lambda it: it.id):
            if item.namespace:
                # Use first dot-segment as the group key
                segment = item.namespace.split(".")[0]
                groups[segment].append(item)
            else:
                groups["_other"].append(item)

        if len(groups) < 2:
            return None

        # Re-split any oversized groups
        result: dict[str, list[SelectableItem]] = {}
        for key, group in sorted(groups.items()):
            if len(group) > self._max_children:
                sub = self._split_by_next_segment(group, key)
                for sub_key, sub_items in sub.items():
                    result[sub_key] = sub_items
            else:
                result[key] = group

        if len(result) > self._max_children:
            return None

        return result

    def _split_by_next_segment(
        self, items: list[SelectableItem], prefix: str
    ) -> dict[str, list[SelectableItem]]:
        """Split a group by the next namespace dot-segment after *prefix*."""
        sub_groups: dict[str, list[SelectableItem]] = defaultdict(list)
        for item in sorted(items, key=lambda it: it.id):
            ns = item.namespace
            rest = ns[len(prefix):].lstrip(".")
            segment = rest.split(".")[0] if rest else "_leaf"
            sub_groups[f"{prefix}.{segment}"].append(item)

        # If still oversized or only 1 group, just chunk alphabetically
        if len(sub_groups) < 2:
            return self._alphabetical_fallback(items)
        return dict(sub_groups)

    # ------------------------------------------------------------------
    # Strategy 2: Clustering (farthest-first Jaccard seeding)
    # ------------------------------------------------------------------

    def _try_clustering(
        self, items: list[SelectableItem]
    ) -> dict[str, list[SelectableItem]] | None:
        """Cluster items by text similarity using farthest-first seeding.

        Returns None if clustering produces degenerate results (all in one
        cluster or too many clusters).
        """
        k = max(2, math.ceil(len(items) / self._target_group_size))
        k = min(k, self._max_children)

        sorted_items = sorted(items, key=lambda it: it.id)
        token_sets = [tokenize(_text_repr(it)) for it in sorted_items]

        # Farthest-first seed selection
        seeds = [0]
        for _ in range(k - 1):
            best_idx = -1
            best_min_dist = -1.0
            for i in range(len(sorted_items)):
                if i in seeds:
                    continue
                min_dist = min(
                    1.0 - jaccard(token_sets[i], token_sets[s])
                    for s in seeds
                )
                if min_dist > best_min_dist:
                    best_min_dist = min_dist
                    best_idx = i
            if best_idx >= 0:
                seeds.append(best_idx)

        # Assign each item to nearest seed
        assignments: dict[int, list[int]] = {s: [] for s in seeds}
        for i in range(len(sorted_items)):
            best_seed = seeds[0]
            best_sim = -1.0
            for s in seeds:
                sim = jaccard(token_sets[i], token_sets[s])
                if sim > best_sim or (sim == best_sim and s < best_seed):
                    best_sim = sim
                    best_seed = s
            assignments[best_seed].append(i)

        # Rebalance: re-split oversized clusters
        groups: dict[str, list[SelectableItem]] = {}
        cluster_idx = 0
        for seed_idx in sorted(assignments):
            members = assignments[seed_idx]
            if not members:
                continue
            cluster_items = [sorted_items[i] for i in members]
            if len(cluster_items) > self._max_children:
                # Sub-split using alphabetical
                chunks = self._chunk_list(cluster_items, self._max_children)
                for chunk in chunks:
                    groups[f"cluster_{cluster_idx:03d}"] = chunk
                    cluster_idx += 1
            else:
                groups[f"cluster_{cluster_idx:03d}"] = cluster_items
                cluster_idx += 1

        if len(groups) < 2:
            return None

        return groups

    # ------------------------------------------------------------------
    # Strategy 3: Alphabetical fallback
    # ------------------------------------------------------------------

    def _alphabetical_fallback(
        self, items: list[SelectableItem]
    ) -> dict[str, list[SelectableItem]]:
        """Sort by name and split into labelled alphabetical chunks."""
        sorted_items = sorted(items, key=lambda it: it.name.lower())
        chunks = self._chunk_list(sorted_items, self._max_children)

        groups: dict[str, list[SelectableItem]] = {}
        for chunk in chunks:
            first = chunk[0].name[0].upper() if chunk[0].name else "?"
            last = chunk[-1].name[0].upper() if chunk[-1].name else "?"
            label = f"{first}-{last}" if first != last else first
            # Ensure unique labels
            base = label
            counter = 1
            while label in groups:
                label = f"{base}_{counter}"
                counter += 1
            groups[label] = chunk

        return groups

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _coalesce_groups(
        self,
        groups: dict[str, list[SelectableItem]],
    ) -> dict[str, list[SelectableItem]]:
        """Merge adjacent groups until the count is <= max_children."""
        entries = sorted(groups.items())
        target = self._max_children
        while len(entries) > target:
            # Merge last two entries into one
            k1, v1 = entries.pop()
            k2, v2 = entries.pop()
            merged_label = f"{k2}+{k1}"
            entries.append((merged_label, v2 + v1))
        return dict(entries)

    @staticmethod
    def _chunk_list(
        items: list[SelectableItem], chunk_size: int
    ) -> list[list[SelectableItem]]:
        """Split *items* into chunks of at most *chunk_size*."""
        k = max(1, math.ceil(len(items) / chunk_size))
        # Distribute as evenly as possible
        base_size = len(items) // k
        remainder = len(items) % k
        chunks: list[list[SelectableItem]] = []
        start = 0
        for i in range(k):
            size = base_size + (1 if i < remainder else 0)
            chunks.append(items[start : start + size])
            start += size
        return chunks

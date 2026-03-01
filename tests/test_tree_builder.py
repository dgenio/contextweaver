"""Tests for contextweaver.routing.tree -- max children, namespace grouping, clustering, alphabetical."""

from __future__ import annotations

import pytest

from contextweaver.exceptions import GraphBuildError
from contextweaver.routing.catalog import generate_sample_catalog, load_catalog_dicts
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import SelectableItem


def _item(
    iid: str, name: str, description: str, namespace: str = "", tags: list[str] | None = None
) -> SelectableItem:
    return SelectableItem(
        id=iid,
        kind="tool",
        name=name,
        description=description,
        namespace=namespace,
        tags=tags or [],
    )


class TestTreeBuilder:
    """Tests for the TreeBuilder class."""

    def test_small_catalog_single_leaf_node(self) -> None:
        items = [
            _item("t1", "search", "Search database"),
            _item("t2", "find", "Find records"),
            _item("t3", "list", "List entries"),
        ]
        builder = TreeBuilder(max_children=10)
        graph = builder.build(items)
        root = graph.nodes[graph.root_id]
        # All items fit directly as children of root
        assert len(root.children) == 3
        assert all(root.child_types[c] == "item" for c in root.children)

    def test_max_children_respected_at_leaf_nodes(self) -> None:
        """Leaf nodes (whose children are items) respect max_children.

        The namespace partition may produce more groups than max_children
        at the root level, but every leaf node that directly contains
        items must have at most max_children item-children.
        """
        catalog = load_catalog_dicts(generate_sample_catalog(n=42, seed=42))
        builder = TreeBuilder(max_children=5)
        graph = builder.build(catalog)
        for node in graph.nodes.values():
            item_children = [c for c in node.children if node.child_types.get(c) == "item"]
            assert len(item_children) <= 5

    def test_namespace_grouping(self) -> None:
        items = [
            _item("b1", "billing.search", "Search invoices", "billing"),
            _item("b2", "billing.create", "Create invoice", "billing"),
            _item("c1", "crm.find", "Find contacts", "crm"),
            _item("c2", "crm.create", "Create contact", "crm"),
            _item("a1", "admin.list", "List users", "admin"),
            _item("a2", "admin.create", "Create user", "admin"),
            _item("s1", "search.docs", "Search docs", "search"),
            _item("s2", "search.web", "Search web", "search"),
            _item("d1", "docs.create", "Create docs", "docs"),
            _item("d2", "docs.list", "List docs", "docs"),
            _item("e1", "email.send", "Send email", "comms"),
        ]
        builder = TreeBuilder(max_children=4)
        graph = builder.build(items)
        # Should create sub-nodes since 11 items > max_children=4
        root = graph.nodes[graph.root_id]
        assert any(root.child_types.get(c) == "node" for c in root.children)

    def test_empty_catalog_raises(self) -> None:
        builder = TreeBuilder()
        with pytest.raises(GraphBuildError, match="empty"):
            builder.build([])

    def test_large_catalog_builds_valid_graph(self) -> None:
        # generate_sample_catalog has 42 total entries across all families,
        # so n=42 is the effective maximum.
        catalog = load_catalog_dicts(generate_sample_catalog(n=42, seed=42))
        builder = TreeBuilder(max_children=10)
        graph = builder.build(catalog)
        stats = graph.graph_stats()
        assert stats["total_items"] == 42
        assert stats["total_nodes"] >= 2
        assert stats["max_depth"] >= 1

    def test_build_meta_populated(self) -> None:
        items = [_item(f"t{i}", f"tool{i}", f"desc {i}") for i in range(15)]
        builder = TreeBuilder(max_children=5)
        graph = builder.build(items)
        assert "version" in graph.build_meta
        assert "item_count" in graph.build_meta
        assert graph.build_meta["item_count"] == 15

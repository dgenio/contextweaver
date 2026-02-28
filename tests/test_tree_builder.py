"""Tests for contextweaver.routing.tree."""

from __future__ import annotations

from contextweaver.routing.catalog import Catalog
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import SelectableItem


def _item(iid: str, name: str, description: str, namespace: str = "") -> SelectableItem:
    return SelectableItem(
        id=iid, kind="tool", name=name, description=description, namespace=namespace
    )


def test_tree_builder_creates_root() -> None:
    catalog = Catalog()
    catalog.register(_item("t1", "search_db", "Search the database"))
    builder = TreeBuilder()
    graph = builder.build(catalog)
    assert "root" in graph.nodes()


def test_tree_builder_namespace_grouping() -> None:
    catalog = Catalog()
    catalog.register(_item("t1", "tool1", "desc", namespace="myns"))
    catalog.register(_item("t2", "tool2", "desc", namespace="myns"))
    builder = TreeBuilder()
    graph = builder.build(catalog)
    assert "ns:myns" in graph.nodes()
    assert "t1" in graph.successors("ns:myns")
    assert "t2" in graph.successors("ns:myns")


def test_tree_builder_category_grouping() -> None:
    catalog = Catalog()
    catalog.register(_item("t1", "search_tool", "Search and find records"))
    builder = TreeBuilder()
    graph = builder.build(catalog)
    # Should have a cat: node
    cat_nodes = [n for n in graph.nodes() if n.startswith("cat:")]
    assert len(cat_nodes) >= 1


def test_empty_catalog() -> None:
    catalog = Catalog()
    builder = TreeBuilder()
    graph = builder.build(catalog)
    assert "root" in graph.nodes()

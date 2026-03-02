"""Tests for contextweaver.routing.tree."""

from __future__ import annotations

import pytest

from contextweaver.exceptions import GraphBuildError
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import SelectableItem


def _item(
    iid: str,
    name: str = "",
    description: str = "desc",
    namespace: str = "",
    tags: list[str] | None = None,
) -> SelectableItem:
    return SelectableItem(
        id=iid,
        kind="tool",
        name=name or iid,
        description=description,
        namespace=namespace,
        tags=tags or [],
    )


# ------------------------------------------------------------------
# Basic construction
# ------------------------------------------------------------------


def test_creates_root() -> None:
    items = [_item("t1", "search_db", "Search the database")]
    graph = TreeBuilder().build(items)
    assert "root" in graph.nodes()


def test_empty_raises() -> None:
    with pytest.raises(GraphBuildError):
        TreeBuilder().build([])


def test_max_children_zero_raises() -> None:
    with pytest.raises(GraphBuildError, match="max_children must be >= 1"):
        TreeBuilder(max_children=0)


def test_max_children_negative_raises() -> None:
    with pytest.raises(GraphBuildError, match="max_children must be >= 1"):
        TreeBuilder(max_children=-5)


def test_target_group_size_zero_raises() -> None:
    with pytest.raises(GraphBuildError, match="target_group_size must be >= 1"):
        TreeBuilder(target_group_size=0)


def test_target_group_size_negative_raises() -> None:
    with pytest.raises(GraphBuildError, match="target_group_size must be >= 1"):
        TreeBuilder(target_group_size=-3)


def test_all_items_reachable() -> None:
    items = [_item(f"t{i}", namespace="ns") for i in range(5)]
    graph = TreeBuilder().build(items)
    visited: set[str] = set()
    stack = [graph.root_id]
    while stack:
        n = stack.pop()
        if n in visited:
            continue
        visited.add(n)
        stack.extend(graph.successors(n))
    for item in items:
        assert item.id in visited, f"Item {item.id} not reachable from root"


def test_determinism() -> None:
    items = [
        _item(f"billing.inv.{i}", namespace="billing") for i in range(30)
    ]
    g1 = TreeBuilder(max_children=10).build(items)
    g2 = TreeBuilder(max_children=10).build(items)
    assert g1.to_dict() == g2.to_dict()


def test_build_meta_populated() -> None:
    items = [_item("t1")]
    graph = TreeBuilder().build(items)
    assert graph.build_meta.get("version") == "1.0"
    assert graph.build_meta.get("item_count") == 1


# ------------------------------------------------------------------
# max_children guarantee
# ------------------------------------------------------------------


def test_max_children_guarantee_small() -> None:
    max_c = 5
    items = [_item(f"t{i:03d}", namespace="ns") for i in range(25)]
    graph = TreeBuilder(max_children=max_c).build(items)
    for nid in graph.nodes():
        children = graph.successors(nid)
        assert len(children) <= max_c, (
            f"Node {nid!r} has {len(children)} children (limit {max_c})"
        )


def test_max_children_guarantee_large() -> None:
    max_c = 10
    items = [
        _item(f"ns{j}.tool{i}", namespace=f"ns{j}")
        for j in range(8)
        for i in range(15)
    ]
    graph = TreeBuilder(max_children=max_c).build(items)
    for nid in graph.nodes():
        assert len(graph.successors(nid)) <= max_c


# ------------------------------------------------------------------
# Strategy 1: Namespace grouping
# ------------------------------------------------------------------


def test_namespace_grouping_two_namespaces() -> None:
    items = [
        _item("billing.inv1", namespace="billing"),
        _item("billing.inv2", namespace="billing"),
        _item("crm.con1", namespace="crm"),
        _item("crm.con2", namespace="crm"),
    ]
    graph = TreeBuilder(max_children=20).build(items)
    root_children = graph.successors("root")
    assert len(root_children) >= 2


def test_namespace_grouping_many_items() -> None:
    max_c = 5
    items = [
        _item(f"billing.sub{i}.tool{j}", namespace="billing")
        for i in range(3)
        for j in range(10)
    ]
    graph = TreeBuilder(max_children=max_c).build(items)
    for nid in graph.nodes():
        assert len(graph.successors(nid)) <= max_c


# ------------------------------------------------------------------
# Strategy 2: Clustering
# ------------------------------------------------------------------


def test_clustering_fallback() -> None:
    items = [
        _item("search_docs", description="Search documents in the archive"),
        _item("search_web", description="Search the public web"),
        _item("create_invoice", description="Create a new billing invoice"),
        _item("send_email", description="Send an email notification"),
        _item("deploy_app", description="Deploy the application"),
        _item("train_model", description="Train a machine learning model"),
    ]
    graph = TreeBuilder(max_children=3).build(items)
    assert len(graph.nodes()) > len(items) + 1
    for nid in graph.nodes():
        assert len(graph.successors(nid)) <= 3


# ------------------------------------------------------------------
# Strategy 3: Alphabetical fallback
# ------------------------------------------------------------------


def test_alphabetical_fallback() -> None:
    items = [_item(f"t{i:03d}", description="same") for i in range(30)]
    graph = TreeBuilder(max_children=10).build(items)
    for nid in graph.nodes():
        assert len(graph.successors(nid)) <= 10


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------


def test_mixed_namespace_and_no_namespace() -> None:
    items = [
        _item("billing.inv1", namespace="billing"),
        _item("billing.inv2", namespace="billing"),
        _item("orphan1"),
        _item("orphan2"),
    ]
    graph = TreeBuilder(max_children=20).build(items)
    assert "root" in graph.nodes()


def test_single_item() -> None:
    graph = TreeBuilder().build([_item("only")])
    assert "root" in graph.nodes()
    visited: set[str] = set()
    stack = [graph.root_id]
    while stack:
        n = stack.pop()
        if n in visited:
            continue
        visited.add(n)
        stack.extend(graph.successors(n))
    assert "only" in visited

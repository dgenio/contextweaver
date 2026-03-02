"""Tests for contextweaver.routing.graph."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from contextweaver.exceptions import GraphBuildError
from contextweaver.routing.graph import ChoiceGraph
from contextweaver.routing.graph_io import load_graph, save_graph
from contextweaver.routing.graph_node import ChoiceNode

# ------------------------------------------------------------------
# ChoiceNode
# ------------------------------------------------------------------


def test_choice_node_defaults() -> None:
    n = ChoiceNode(node_id="n1")
    assert n.node_id == "n1"
    assert n.label == ""
    assert n.children == []
    assert n.child_types == {}


def test_choice_node_roundtrip() -> None:
    n = ChoiceNode(
        node_id="n1",
        label="search",
        routing_hint="Search tools",
        children=["c1", "c2"],
        child_types={"c1": "node", "c2": "item"},
    )
    restored = ChoiceNode.from_dict(n.to_dict())
    assert restored.node_id == n.node_id
    assert restored.label == n.label
    assert restored.children == n.children
    assert restored.child_types == n.child_types


# ------------------------------------------------------------------
# Basic graph operations
# ------------------------------------------------------------------


def test_add_node() -> None:
    g = ChoiceGraph()
    g.add_node("a")
    assert "a" in g.nodes()


def test_add_node_idempotent() -> None:
    g = ChoiceGraph()
    g.add_node("a", label="first")
    g.add_node("a", label="updated")
    assert g.get_node("a").label == "updated"


def test_add_edge_creates_nodes() -> None:
    g = ChoiceGraph()
    g.add_edge("a", "b")
    assert "a" in g.nodes()
    assert "b" in g.nodes()


def test_successors() -> None:
    g = ChoiceGraph()
    g.add_edge("a", "b")
    g.add_edge("a", "c")
    assert g.successors("a") == ["b", "c"]


def test_predecessors() -> None:
    g = ChoiceGraph()
    g.add_edge("a", "c")
    g.add_edge("b", "c")
    assert g.predecessors("c") == ["a", "b"]


def test_roots() -> None:
    g = ChoiceGraph()
    g.add_edge("root", "a")
    g.add_edge("root", "b")
    g.add_edge("a", "c")
    assert g.roots() == ["root"]


def test_topological_order() -> None:
    g = ChoiceGraph()
    g.add_edge("a", "b")
    g.add_edge("b", "c")
    order = g.topological_order()
    assert order.index("a") < order.index("b") < order.index("c")


def test_get_node_missing_raises() -> None:
    g = ChoiceGraph()
    with pytest.raises(GraphBuildError):
        g.get_node("missing")


# ------------------------------------------------------------------
# Cycle detection
# ------------------------------------------------------------------


def test_cycle_detection() -> None:
    g = ChoiceGraph()
    g.add_edge("a", "b")
    g.add_edge("b", "c")
    with pytest.raises(GraphBuildError, match="cycle"):
        g.add_edge("c", "a")


def test_self_loop_detection() -> None:
    g = ChoiceGraph()
    g.add_node("a")
    with pytest.raises(GraphBuildError, match="cycle"):
        g.add_edge("a", "a")


# ------------------------------------------------------------------
# Items
# ------------------------------------------------------------------


def test_add_item() -> None:
    g = ChoiceGraph()
    g.add_item("tool-1")
    assert "tool-1" in g.items()


def test_items_sorted() -> None:
    g = ChoiceGraph()
    g.add_item("z")
    g.add_item("a")
    g.add_item("m")
    assert g.items() == ["a", "m", "z"]


def test_edge_child_type_item() -> None:
    g = ChoiceGraph()
    g.add_item("tool-1")
    g.add_node("root")
    g.add_edge("root", "tool-1")
    node = g.get_node("root")
    assert node.child_types.get("tool-1") == "item"


def test_edge_child_type_node() -> None:
    g = ChoiceGraph()
    g.add_node("root")
    g.add_node("group")
    g.add_edge("root", "group")
    node = g.get_node("root")
    assert node.child_types.get("group") == "node"


# ------------------------------------------------------------------
# Serialisation round-trip (to_dict / from_dict)
# ------------------------------------------------------------------


def test_roundtrip() -> None:
    g = ChoiceGraph()
    g.add_edge("root", "ns:data")
    g.add_edge("ns:data", "tool1")
    restored = ChoiceGraph.from_dict(g.to_dict())
    assert "tool1" in restored.nodes()
    assert "tool1" in restored.successors("ns:data")


def test_roundtrip_with_items() -> None:
    g = ChoiceGraph()
    g.add_item("tool-1")
    g.add_item("tool-2")
    g.add_node("root")
    g.add_edge("root", "tool-1")
    g.add_edge("root", "tool-2")
    g.root_id = "root"
    g.build_meta = {"version": "1.0", "strategy": "test"}

    restored = ChoiceGraph.from_dict(g.to_dict())
    assert sorted(restored.items()) == ["tool-1", "tool-2"]
    assert restored.root_id == "root"
    assert restored.build_meta["version"] == "1.0"


def test_roundtrip_children_child_types_consistent() -> None:
    """from_dict rebuilds children/child_types from edges."""
    g = ChoiceGraph()
    g.add_item("billing.inv")
    g.add_node("root")
    g.add_node("group")
    g.add_edge("root", "group")
    g.add_edge("group", "billing.inv")
    g.root_id = "root"

    restored = ChoiceGraph.from_dict(g.to_dict())
    root = restored.get_node("root")
    assert root.children == ["group"]
    assert root.child_types == {"group": "node"}

    grp = restored.get_node("group")
    assert grp.children == ["billing.inv"]
    assert grp.child_types == {"billing.inv": "item"}


def test_roundtrip_deterministic_keys() -> None:
    g = ChoiceGraph()
    g.add_edge("root", "z_node")
    g.add_edge("root", "a_node")
    d = g.to_dict()
    # Keys in nodes and edges dicts should be sorted
    assert list(d["nodes"].keys()) == sorted(d["nodes"].keys())
    assert list(d["edges"].keys()) == sorted(d["edges"].keys())


def test_from_dict_cycle_discards_edge() -> None:
    """from_dict must remove the cycle-causing edge before raising."""
    data: dict[str, Any] = {
        "root_id": "root",
        "nodes": {
            "root": {"node_id": "root", "label": "root"},
            "a": {"node_id": "a", "label": "a"},
        },
        "items": [],
        "edges": {
            "root": ["a"],
            "a": ["root"],  # creates a cycle
        },
    }
    with pytest.raises(GraphBuildError, match="Cycle detected"):
        ChoiceGraph.from_dict(data)


# ------------------------------------------------------------------
# File I/O (save / load)
# ------------------------------------------------------------------


def test_save_and_load() -> None:
    g = ChoiceGraph()
    g.add_item("tool-a")
    g.add_node("root")
    g.add_edge("root", "tool-a")
    g.root_id = "root"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        path = f.name
    try:
        save_graph(g, path)
        loaded = load_graph(path)
        assert "tool-a" in loaded.items()
        assert loaded.root_id == "root"
    finally:
        Path(path).unlink()


def test_load_bad_file_raises() -> None:
    with pytest.raises(GraphBuildError, match="Cannot read"):
        load_graph("/nonexistent/graph.json")


def test_load_invalid_json_raises() -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        f.write("{bad json")
        path = f.name
    try:
        with pytest.raises(GraphBuildError, match="Invalid JSON"):
            load_graph(path)
    finally:
        Path(path).unlink()


# ------------------------------------------------------------------
# Validation (load validates)
# ------------------------------------------------------------------


def test_validate_bad_root_raises() -> None:
    g = ChoiceGraph()
    g.add_node("root")
    g.root_id = "missing_root"
    d = g.to_dict()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(d, f)
        path = f.name
    try:
        with pytest.raises(GraphBuildError, match="Root node"):
            load_graph(path)
    finally:
        Path(path).unlink()


def test_validate_unreachable_item_raises() -> None:
    g = ChoiceGraph()
    g.add_node("root")
    g.add_item("orphan")
    g.add_node("group")
    g.add_edge("root", "group")
    g.root_id = "root"
    d = g.to_dict()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(d, f)
        path = f.name
    try:
        with pytest.raises(GraphBuildError, match="not reachable"):
            load_graph(path)
    finally:
        Path(path).unlink()


def test_validate_bad_child_ref_raises() -> None:
    # Test that a cycle in the graph raises on load.
    d_cycle = {
        "root_id": "root",
        "nodes": {
            "root": {
                "node_id": "root",
                "label": "root",
                "routing_hint": "",
                "children": ["a"],
                "child_types": {"a": "node"},
                "stats": {},
            },
            "a": {
                "node_id": "a",
                "label": "a",
                "routing_hint": "",
                "children": ["root"],
                "child_types": {"root": "node"},
                "stats": {},
            },
        },
        "items": [],
        "edges": {"root": ["a"], "a": ["root"]},
        "max_children": 20,
        "build_meta": {},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(d_cycle, f)
        path = f.name
    try:
        with pytest.raises(GraphBuildError):
            load_graph(path)
    finally:
        Path(path).unlink()


# ------------------------------------------------------------------
# Stats
# ------------------------------------------------------------------


def test_stats_basic() -> None:
    g = ChoiceGraph()
    g.add_node("root")
    g.root_id = "root"
    g.add_item("billing.inv1")
    g.add_item("crm.contact1")
    g.add_node("group1")
    g.add_edge("root", "group1")
    g.add_edge("group1", "billing.inv1")
    g.add_edge("group1", "crm.contact1")

    s = g.stats()
    assert s["total_items"] == 2
    # total_nodes counts only navigation nodes (root + group1), not items
    assert s["total_nodes"] == 2
    assert s["max_depth"] >= 1
    assert "avg_branching_factor" in s
    assert "max_branching_factor" in s
    # leaf_node_count: nav nodes with zero outgoing edges (group1 has
    # edges to items, so neither root nor group1 is a leaf here)
    assert s["leaf_node_count"] == 0
    assert "namespaces" in s


def test_stats_namespaces() -> None:
    g = ChoiceGraph()
    g.add_node("root")
    g.root_id = "root"
    g.add_item("billing.inv1")
    g.add_item("crm.contact1")
    g.add_item("search.web")

    s = g.stats()
    assert set(s["namespaces"]) == {"billing", "crm", "search"}

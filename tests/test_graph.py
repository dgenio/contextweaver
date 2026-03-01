"""Tests for contextweaver.routing.graph -- ChoiceGraph save/load round-trip, validation, graph_stats."""

from __future__ import annotations

import tempfile

import pytest

from contextweaver.exceptions import GraphBuildError
from contextweaver.routing.graph import ChoiceGraph, ChoiceNode
from contextweaver.types import SelectableItem


def _build_simple_graph() -> ChoiceGraph:
    """Build a simple graph with root -> 2 children nodes -> items."""
    graph = ChoiceGraph(root_id="root", max_children=10)
    graph.items["t1"] = SelectableItem(
        id="t1", kind="tool", name="t1", description="tool 1", namespace="ns1"
    )
    graph.items["t2"] = SelectableItem(
        id="t2", kind="tool", name="t2", description="tool 2", namespace="ns1"
    )
    graph.items["t3"] = SelectableItem(
        id="t3", kind="tool", name="t3", description="tool 3", namespace="ns2"
    )
    graph.nodes["root"] = ChoiceNode(
        node_id="root",
        label="root",
        routing_hint="All tools",
        children=["g0", "g1"],
        child_types={"g0": "node", "g1": "node"},
    )
    graph.nodes["g0"] = ChoiceNode(
        node_id="g0",
        label="ns1",
        routing_hint="NS1 tools",
        children=["t1", "t2"],
        child_types={"t1": "item", "t2": "item"},
    )
    graph.nodes["g1"] = ChoiceNode(
        node_id="g1",
        label="ns2",
        routing_hint="NS2 tools",
        children=["t3"],
        child_types={"t3": "item"},
    )
    return graph


class TestChoiceGraph:
    """Tests for ChoiceGraph."""

    def test_save_and_load_round_trip(self) -> None:
        graph = _build_simple_graph()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            graph.save(f.name)
            loaded = ChoiceGraph.load(f.name)
        assert loaded.root_id == "root"
        assert set(loaded.nodes.keys()) == {"root", "g0", "g1"}
        assert set(loaded.items.keys()) == {"t1", "t2", "t3"}

    def test_to_dict_from_dict_round_trip(self) -> None:
        graph = _build_simple_graph()
        d = graph.to_dict()
        restored = ChoiceGraph.from_dict(d)
        assert restored.root_id == graph.root_id
        assert set(restored.nodes.keys()) == set(graph.nodes.keys())
        assert set(restored.items.keys()) == set(graph.items.keys())
        assert restored.max_children == graph.max_children

    def test_validation_missing_root_raises(self) -> None:
        d = {
            "root_id": "nonexistent",
            "nodes": {},
            "items": {},
        }
        with pytest.raises(GraphBuildError, match="root_id"):
            ChoiceGraph.from_dict(d)

    def test_validation_missing_child_node_raises(self) -> None:
        d = {
            "root_id": "root",
            "nodes": {
                "root": {
                    "node_id": "root",
                    "label": "root",
                    "routing_hint": "hint",
                    "children": ["missing_node"],
                    "child_types": {"missing_node": "node"},
                    "stats": {},
                }
            },
            "items": {},
        }
        with pytest.raises(GraphBuildError, match="missing child node"):
            ChoiceGraph.from_dict(d)

    def test_validation_missing_child_item_raises(self) -> None:
        d = {
            "root_id": "root",
            "nodes": {
                "root": {
                    "node_id": "root",
                    "label": "root",
                    "routing_hint": "hint",
                    "children": ["missing_item"],
                    "child_types": {"missing_item": "item"},
                    "stats": {},
                }
            },
            "items": {},
        }
        with pytest.raises(GraphBuildError, match="missing child item"):
            ChoiceGraph.from_dict(d)

    def test_validation_cycle_detection(self) -> None:
        d = {
            "root_id": "root",
            "nodes": {
                "root": {
                    "node_id": "root",
                    "label": "root",
                    "routing_hint": "hint",
                    "children": ["child"],
                    "child_types": {"child": "node"},
                    "stats": {},
                },
                "child": {
                    "node_id": "child",
                    "label": "child",
                    "routing_hint": "hint",
                    "children": ["root"],
                    "child_types": {"root": "node"},
                    "stats": {},
                },
            },
            "items": {},
        }
        with pytest.raises(GraphBuildError, match="Cycle"):
            ChoiceGraph.from_dict(d)

    def test_graph_stats(self) -> None:
        graph = _build_simple_graph()
        stats = graph.graph_stats()
        assert stats["total_items"] == 3
        assert stats["total_nodes"] == 3
        assert stats["max_depth"] >= 1
        assert stats["max_branching_factor"] == 2
        assert stats["leaf_node_count"] == 2
        assert "ns1" in stats["namespaces"]
        assert "ns2" in stats["namespaces"]

    def test_graph_stats_empty_graph(self) -> None:
        graph = ChoiceGraph()
        graph.nodes = {}
        stats = graph.graph_stats()
        assert stats["total_items"] == 0
        assert stats["total_nodes"] == 0

    def test_load_invalid_file_raises(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not json")
            f.flush()
        with pytest.raises(GraphBuildError, match="Failed to load"):
            ChoiceGraph.load(f.name)

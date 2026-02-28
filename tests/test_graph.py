"""Tests for contextweaver.routing.graph."""

from __future__ import annotations

import pytest

from contextweaver.exceptions import GraphBuildError
from contextweaver.routing.graph import ChoiceGraph


def test_add_node() -> None:
    g = ChoiceGraph()
    g.add_node("a")
    assert "a" in g.nodes()


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


def test_cycle_detection() -> None:
    g = ChoiceGraph()
    g.add_edge("a", "b")
    g.add_edge("b", "c")
    with pytest.raises(GraphBuildError):
        g.add_edge("c", "a")


def test_roundtrip() -> None:
    g = ChoiceGraph()
    g.add_edge("root", "ns:data")
    g.add_edge("ns:data", "tool1")
    restored = ChoiceGraph.from_dict(g.to_dict())
    assert "tool1" in restored.nodes()
    assert "tool1" in restored.successors("ns:data")

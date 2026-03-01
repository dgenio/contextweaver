"""Tests for contextweaver.routing.router -- Router beam search, top_k, confidence_gap bounds, determinism."""

from __future__ import annotations

import pytest

from contextweaver.exceptions import RouteError
from contextweaver.routing.catalog import generate_sample_catalog, load_catalog_dicts
from contextweaver.routing.router import Router, RouteResult
from contextweaver.routing.tree import TreeBuilder


class TestRouter:
    """Tests for the Router beam-search class."""

    def test_basic_route(self, sample_graph) -> None:
        router = Router(sample_graph)
        result = router.route("search invoices billing")
        assert isinstance(result, RouteResult)
        assert len(result.candidate_items) > 0
        assert len(result.candidate_ids) > 0

    def test_top_k_limit(self, sample_graph) -> None:
        router = Router(sample_graph, top_k=3)
        result = router.route("search invoices")
        assert len(result.candidate_items) <= 3

    def test_deterministic(self, sample_graph) -> None:
        router = Router(sample_graph, beam_width=2)
        r1 = router.route("search database records")
        r2 = router.route("search database records")
        assert r1.candidate_ids == r2.candidate_ids
        assert r1.scores == r2.scores

    def test_confidence_gap_bounds(self) -> None:
        catalog = load_catalog_dicts(generate_sample_catalog(n=50, seed=42))
        graph = TreeBuilder(max_children=10).build(catalog)
        # Valid confidence gap
        Router(graph, confidence_gap=0.0)
        Router(graph, confidence_gap=1.0)
        # Invalid confidence gap
        with pytest.raises(ValueError, match="confidence_gap"):
            Router(graph, confidence_gap=-0.1)
        with pytest.raises(ValueError, match="confidence_gap"):
            Router(graph, confidence_gap=1.5)

    def test_empty_graph_raises(self) -> None:
        from contextweaver.routing.graph import ChoiceGraph

        graph = ChoiceGraph()
        graph.nodes = {}
        router = Router(graph)
        with pytest.raises(RouteError, match="no nodes"):
            router.route("query")

    def test_scores_populated(self, sample_graph) -> None:
        router = Router(sample_graph)
        result = router.route("billing invoices search")
        for cid in result.candidate_ids:
            assert cid in result.scores

    def test_debug_trace(self, sample_graph) -> None:
        router = Router(sample_graph)
        result = router.route("billing invoices", debug=True)
        assert result.debug_trace is not None
        assert len(result.debug_trace) > 0
        assert "depth" in result.debug_trace[0]
        assert "node" in result.debug_trace[0]

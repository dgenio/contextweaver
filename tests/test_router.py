"""Tests for contextweaver.routing.router."""

from __future__ import annotations

import pytest

from contextweaver.exceptions import RouteError
from contextweaver.routing.graph import ChoiceGraph
from contextweaver.routing.router import Router, RouteResult
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


def _build_catalog_items() -> list[SelectableItem]:
    return [
        _item("db_read", "read_db", "Read from database", tags=["data", "read"]),
        _item("db_write", "write_db", "Write to database", tags=["data", "write"]),
        _item("send_email", "send_email", "Send email notification", tags=["comm", "email"]),
        _item("search_docs", "search_docs", "Search documentation pages", tags=["search", "docs"]),
        _item("create_user", "create_user", "Create a new user account", tags=["admin", "users"]),
    ]


def _setup_router(
    items: list[SelectableItem] | None = None,
    beam_width: int = 2,
    top_k: int = 20,
    confidence_gap: float = 0.15,
) -> Router:
    items = items or _build_catalog_items()
    graph = TreeBuilder(max_children=20).build(items)
    router = Router(
        graph,
        beam_width=beam_width,
        top_k=top_k,
        confidence_gap=confidence_gap,
    )
    router.set_items(items)
    return router


# ------------------------------------------------------------------
# Basic routing
# ------------------------------------------------------------------


def test_route_returns_route_result() -> None:
    router = _setup_router()
    result = router.route("database")
    assert isinstance(result, RouteResult)
    assert len(result.candidate_ids) >= 1


def test_route_candidate_items_match_ids() -> None:
    router = _setup_router()
    result = router.route("database read")
    assert len(result.candidate_items) == len(result.candidate_ids)
    for item, iid in zip(result.candidate_items, result.candidate_ids, strict=False):
        assert item.id == iid


def test_route_scores_length_matches() -> None:
    router = _setup_router()
    result = router.route("search")
    assert len(result.scores) == len(result.candidate_ids)


# ------------------------------------------------------------------
# Determinism
# ------------------------------------------------------------------


def test_determinism() -> None:
    router = _setup_router()
    r1 = router.route("read database")
    r2 = router.route("read database")
    assert r1.candidate_ids == r2.candidate_ids
    assert r1.scores == r2.scores


def test_determinism_large() -> None:
    items = [
        _item(f"ns{j}.tool{i}", name=f"tool_{i}_{j}", namespace=f"ns{j}",
               description=f"Tool {i} in namespace {j}")
        for j in range(5)
        for i in range(10)
    ]
    router = _setup_router(items=items, beam_width=3)
    r1 = router.route("tool namespace search")
    r2 = router.route("tool namespace search")
    assert r1.candidate_ids == r2.candidate_ids


# ------------------------------------------------------------------
# beam_width and top_k
# ------------------------------------------------------------------


def test_top_k_limits_results() -> None:
    items = [
        _item(f"t{i}", description=f"Database tool {i}", tags=["data"])
        for i in range(30)
    ]
    router = _setup_router(items=items, top_k=5)
    result = router.route("database tool")
    assert len(result.candidate_ids) <= 5


# ------------------------------------------------------------------
# confidence_gap bounds
# ------------------------------------------------------------------


def test_confidence_gap_valid_range() -> None:
    items = _build_catalog_items()
    graph = TreeBuilder().build(items)
    # Valid extremes
    Router(graph, confidence_gap=0.0)
    Router(graph, confidence_gap=1.0)


def test_confidence_gap_below_zero_raises() -> None:
    items = _build_catalog_items()
    graph = TreeBuilder().build(items)
    with pytest.raises(ValueError, match="confidence_gap"):
        Router(graph, confidence_gap=-0.1)


def test_confidence_gap_above_one_raises() -> None:
    items = _build_catalog_items()
    graph = TreeBuilder().build(items)
    with pytest.raises(ValueError, match="confidence_gap"):
        Router(graph, confidence_gap=1.5)


# ------------------------------------------------------------------
# Error handling
# ------------------------------------------------------------------


def test_empty_graph_raises() -> None:
    graph = ChoiceGraph()
    graph.add_node("root")
    graph.root_id = "nonexistent"
    router = Router(graph)
    with pytest.raises(RouteError):
        router.route("anything")


# ------------------------------------------------------------------
# Debug trace
# ------------------------------------------------------------------


def test_debug_trace_populated() -> None:
    router = _setup_router()
    result = router.route("database", debug=True)
    assert len(result.debug_trace) >= 1
    assert "depth" in result.debug_trace[0]


def test_debug_trace_empty_when_not_requested() -> None:
    router = _setup_router()
    result = router.route("database", debug=False)
    assert result.debug_trace == []


# ------------------------------------------------------------------
# Backtracking
# ------------------------------------------------------------------


def test_backtrack_expands_unexplored() -> None:
    """With a narrow beam, backtracking should still find relevant items."""
    items = [
        _item("billing.inv1", name="invoice_create", description="Create invoice",
              namespace="billing", tags=["billing"]),
        _item("billing.inv2", name="invoice_search", description="Search invoices",
              namespace="billing", tags=["billing", "search"]),
        _item("crm.contact1", name="contact_find", description="Find contacts",
              namespace="crm", tags=["crm"]),
    ]
    router = _setup_router(items=items, beam_width=1)
    result = router.route("invoice billing")
    # Should find at least one billing item
    assert any("billing" in cid for cid in result.candidate_ids)

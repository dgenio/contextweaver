"""Tests for contextweaver.routing.router."""

from __future__ import annotations

import pytest

from contextweaver.exceptions import ConfigError, RouteError
from contextweaver.profiles import RoutingConfig
from contextweaver.routing.filters import compose_shortlist
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
    return Router(
        graph,
        items=items,
        beam_width=beam_width,
        top_k=top_k,
        confidence_gap=confidence_gap,
    )


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
        _item(
            f"ns{j}.tool{i}",
            name=f"tool_{i}_{j}",
            namespace=f"ns{j}",
            description=f"Tool {i} in namespace {j}",
        )
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
    items = [_item(f"t{i}", description=f"Database tool {i}", tags=["data"]) for i in range(30)]
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
    with pytest.raises(ConfigError, match="confidence_gap"):
        Router(graph, confidence_gap=-0.1)


def test_confidence_gap_above_one_raises() -> None:
    items = _build_catalog_items()
    graph = TreeBuilder().build(items)
    with pytest.raises(ConfigError, match="confidence_gap"):
        Router(graph, confidence_gap=1.5)


# ------------------------------------------------------------------
# Pluggable scorer backends
# ------------------------------------------------------------------


def test_router_bm25_backend_routes() -> None:
    items = _build_catalog_items()
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items, scorer_backend="bm25", top_k=3)
    result = router.route("send email to user")
    assert len(result.candidate_ids) > 0
    assert "send_email" in result.candidate_ids


def test_router_tfidf_backend_default() -> None:
    items = _build_catalog_items()
    graph = TreeBuilder().build(items)
    # Default backend should still be tfidf for backward compatibility.
    router = Router(graph, items=items, top_k=3)
    result = router.route("read database")
    assert len(result.candidate_ids) > 0


def test_router_unknown_backend_raises() -> None:
    items = _build_catalog_items()
    graph = TreeBuilder().build(items)
    from contextweaver.exceptions import ConfigError

    with pytest.raises(ConfigError, match="scorer_backend"):
        Router(graph, items=items, scorer_backend="not-a-backend")


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


def test_no_items_raises() -> None:
    items = _build_catalog_items()
    graph = TreeBuilder().build(items)
    router = Router(graph)  # no items kwarg, no set_items()
    with pytest.raises(RouteError, match="No items registered"):
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
        _item(
            "billing.inv1",
            name="invoice_create",
            description="Create invoice",
            namespace="billing",
            tags=["billing"],
        ),
        _item(
            "billing.inv2",
            name="invoice_search",
            description="Search invoices",
            namespace="billing",
            tags=["billing", "search"],
        ),
        _item(
            "crm.contact1",
            name="contact_find",
            description="Find contacts",
            namespace="crm",
            tags=["crm"],
        ),
    ]
    router = _setup_router(items=items, beam_width=1)
    result = router.route("invoice billing")
    # Should find at least one billing item
    assert any("billing" in cid for cid in result.candidate_ids)


def test_backtrack_fills_up_to_top_k() -> None:
    """Backtracking should return close to top_k on a well-populated catalog."""
    items = [
        _item(
            f"ns{j}.tool{i}",
            name=f"tool_{i}_{j}",
            namespace=f"ns{j}",
            description=f"Tool {i} in namespace {j}",
            tags=[f"ns{j}", "tool"],
        )
        for j in range(5)
        for i in range(10)
    ]
    top_k = 15
    router = _setup_router(items=items, beam_width=2, top_k=top_k)
    result = router.route("tool namespace")
    # With 50 items and top_k=15, backtracking should fill well past beam_width=2
    assert len(result.candidate_ids) >= top_k // 2
    assert len(result.candidate_ids) <= top_k


# ------------------------------------------------------------------
# routing_config override
# ------------------------------------------------------------------


def test_routing_config_overrides_defaults() -> None:
    """routing_config sets all four beam-search params when supplied."""
    items = _build_catalog_items()
    graph = TreeBuilder().build(items)
    rc = RoutingConfig(beam_width=3, max_depth=6, top_k=7, confidence_gap=0.12)
    router = Router(graph, routing_config=rc)
    assert router._beam_width == 3
    assert router._max_depth == 6
    assert router._top_k == 7
    assert router._confidence_gap == 0.12


def test_routing_config_overrides_explicit_kwargs() -> None:
    """routing_config takes priority over individually supplied positional kwargs."""
    items = _build_catalog_items()
    graph = TreeBuilder().build(items)
    rc = RoutingConfig(beam_width=3, max_depth=6, top_k=7, confidence_gap=0.12)
    # beam_width=99 and top_k=50 are overridden by routing_config
    router = Router(graph, beam_width=99, top_k=50, routing_config=rc)
    assert router._beam_width == 3
    assert router._top_k == 7


# ------------------------------------------------------------------
# Issue #112 — Negative routing (exclude_ids / exclude_tags)
# ------------------------------------------------------------------


def test_exclude_ids_drops_listed_items() -> None:
    router = _setup_router()
    full = router.route("database read")
    assert "db_read" in full.candidate_ids
    filtered = router.route("database read", exclude_ids={"db_read"})
    assert "db_read" not in filtered.candidate_ids
    assert filtered.excluded_count >= 1


def test_exclude_tags_drops_tagged_items() -> None:
    router = _setup_router()
    full = router.route("database")
    assert any(cid.startswith("db_") for cid in full.candidate_ids)
    filtered = router.route("database", exclude_tags={"data"})
    assert all(not cid.startswith("db_") for cid in filtered.candidate_ids)
    assert filtered.excluded_count >= 2


def test_exclude_all_raises_route_error() -> None:
    items = _build_catalog_items()
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items)
    all_ids = {it.id for it in items}
    with pytest.raises(RouteError, match="filtered out"):
        router.route("anything", exclude_ids=all_ids)


def test_exclude_count_reported_in_trace() -> None:
    router = _setup_router()
    result = router.route("database", exclude_ids={"db_read"})
    assert result.trace.excluded_count == result.excluded_count
    assert result.trace.excluded_count >= 1


# ------------------------------------------------------------------
# Issue #116 — Context-aware shortlisting (context_hints)
# ------------------------------------------------------------------


def test_context_hints_change_ranking() -> None:
    items = [
        _item("send_email", "send_email", "Send notification", tags=["comm"]),
        _item("send_sms", "send_sms", "Send notification", tags=["comm"]),
    ]
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items, top_k=2)
    # Without hints, the names match symmetrically; hints break the tie.
    result = router.route("send notification", context_hints=["email"])
    assert result.candidate_ids[0] == "send_email"


def test_context_hints_none_is_noop() -> None:
    router = _setup_router()
    r1 = router.route("database read")
    r2 = router.route("database read", context_hints=None)
    r3 = router.route("database read", context_hints=[])
    assert r1.candidate_ids == r2.candidate_ids == r3.candidate_ids


def test_context_hints_strip_whitespace() -> None:
    """Whitespace-only hints must not change scoring."""
    router = _setup_router()
    r1 = router.route("database read")
    r2 = router.route("database read", context_hints=["   ", "\t"])
    assert r1.candidate_ids == r2.candidate_ids


# ------------------------------------------------------------------
# Issue #22 — Toolset gating (allowed_namespaces / allowed_tags)
# ------------------------------------------------------------------


def test_allowed_namespaces_whitelists_namespace() -> None:
    items = [
        _item("billing.invoice", namespace="billing", description="invoice tool"),
        _item("comms.email", namespace="comms", description="email tool"),
    ]
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items)
    result = router.route("tool", allowed_namespaces={"billing"})
    assert result.candidate_ids == ["billing.invoice"]
    assert result.gated_count == 1


def test_allowed_tags_whitelists_tags() -> None:
    items = [
        _item("a", tags=["read"], description="alpha"),
        _item("b", tags=["write"], description="beta"),
    ]
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items)
    result = router.route("anything", allowed_tags={"read"})
    assert result.candidate_ids == ["a"]
    assert result.gated_count == 1


def test_gating_combined_with_exclusion() -> None:
    items = [
        _item("billing.a", namespace="billing", tags=["read"]),
        _item("billing.b", namespace="billing", tags=["write"]),
        _item("comms.x", namespace="comms", tags=["read"]),
    ]
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items)
    # Allow billing namespace, exclude write tag → only billing.a remains.
    result = router.route(
        "anything",
        allowed_namespaces={"billing"},
        exclude_tags={"write"},
    )
    assert result.candidate_ids == ["billing.a"]
    assert result.gated_count == 1
    assert result.excluded_count == 1


# ------------------------------------------------------------------
# Issue #14 — Uncertainty & clarifying questions
# ------------------------------------------------------------------


def test_unambiguous_route_marks_not_ambiguous() -> None:
    items = [
        _item("billing.invoice", "invoice", "invoice billing tool", tags=["billing"]),
        _item("storage.archive", "archive", "archive storage", tags=["archive"]),
    ]
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items, confidence_gap=0.05)
    result = router.route("invoice billing")
    assert result.is_ambiguous is False
    assert result.clarifying_question is None


def test_ambiguous_route_emits_clarifying_question() -> None:
    items = [
        _item("ns_a.tool", "tool", "shared tool description", namespace="ns_a"),
        _item("ns_b.tool", "tool", "shared tool description", namespace="ns_b"),
    ]
    graph = TreeBuilder().build(items)
    # Wide gap so equal-scoring candidates trigger ambiguity.
    router = Router(graph, items=items, confidence_gap=0.5)
    result = router.route("tool")
    assert result.is_ambiguous is True
    assert result.clarifying_question is not None
    assert "ns_a" in result.clarifying_question or "ns_b" in result.clarifying_question


def test_trace_records_top_and_runner_up_scores() -> None:
    router = _setup_router()
    result = router.route("database read")
    assert result.trace.top_score == result.scores[0]
    if len(result.scores) >= 2:
        assert result.trace.runner_up_score == result.scores[1]
    else:
        assert result.trace.runner_up_score is None


# ------------------------------------------------------------------
# Issue #51 — Structured RouteTrace
# ------------------------------------------------------------------


def test_trace_always_present() -> None:
    router = _setup_router()
    result = router.route("database")
    # Trace is non-None regardless of debug flag.
    assert result.trace is not None
    assert result.trace.query == "database"
    assert result.trace.confidence_gap == router._confidence_gap


def test_trace_steps_only_with_debug() -> None:
    router = _setup_router()
    no_debug = router.route("database", debug=False)
    debug = router.route("database", debug=True)
    assert no_debug.trace.steps == []
    assert len(debug.trace.steps) >= 1


def test_legacy_debug_trace_preserved() -> None:
    """The legacy ``debug_trace`` list-of-dicts shape still works."""
    router = _setup_router()
    result = router.route("database", debug=True)
    assert result.debug_trace
    first = result.debug_trace[0]
    assert "depth" in first
    assert "expansions" in first


def test_trace_round_trip_serialisation() -> None:
    from contextweaver import RouteTrace

    router = _setup_router()
    original = router.route("database", debug=True).trace
    restored = RouteTrace.from_dict(original.to_dict())
    assert restored.query == original.query
    assert restored.confidence_gap == original.confidence_gap
    assert restored.is_ambiguous == original.is_ambiguous
    assert len(restored.steps) == len(original.steps)


# ------------------------------------------------------------------
# Pre-scoring filter regressions
# ------------------------------------------------------------------


def test_excluded_leaves_do_not_consume_beam_slots() -> None:
    """Excluded leaves must not displace eligible siblings in the beam.

    Regression for the pre-scoring exclusion contract: with a tight
    beam, an excluded item that scores highest on the query should
    not crowd out a lower-scoring eligible sibling.
    """
    items = [
        _item("db_read", "read database", "Read database rows", tags=["data"]),
        _item("db_write", "write database", "Write database rows", tags=["data"]),
        _item("send_email", "send email", "Send email", tags=["comm"]),
    ]
    graph = TreeBuilder(max_children=20).build(items)
    # beam_width=1 forces displacement: only one leaf survives the beam
    # at the depth where leaves are the children of root.
    router = Router(graph, items=items, beam_width=1, top_k=3)
    full = router.route("database")
    assert "db_read" in full.candidate_ids
    excluded = router.route("database", exclude_ids={"db_read"})
    assert "db_read" not in excluded.candidate_ids
    # The eligible sibling must surface even though the excluded item
    # would have outscored it in the unfiltered beam.
    assert "db_write" in excluded.candidate_ids
    assert excluded.excluded_count == 1


def test_internal_subtree_pruned_when_all_descendants_excluded() -> None:
    """Internal nodes with no eligible descendants are skipped pre-scoring.

    Regression: with a graph that buckets items by namespace, excluding
    every item under one namespace must not let that empty subtree
    consume beam slots.
    """
    items = [
        _item("billing.invoice", namespace="billing", tags=["finance"]),
        _item("billing.refund", namespace="billing", tags=["finance"]),
        _item("comms.email", namespace="comms", tags=["comm"]),
    ]
    graph = TreeBuilder(max_children=2).build(items)
    router = Router(graph, items=items, beam_width=1, top_k=3)
    # Exclude every item under "billing" so the entire billing subtree
    # is ineligible. The single remaining leaf (comms.email) must still
    # be reachable through the surviving beam.
    result = router.route("billing invoice", exclude_ids={"billing.invoice", "billing.refund"})
    assert result.candidate_ids == ["comms.email"]
    assert result.excluded_count == 2


# ------------------------------------------------------------------
# Issue #14 regression — top_k=1 must still detect ambiguity
# ------------------------------------------------------------------


def test_top_k_one_still_detects_ambiguity() -> None:
    """``top_k=1`` callers must still see ``is_ambiguous`` and a runner-up score.

    Regression: ambiguity is computed from the untrimmed sorted view,
    so trimming candidates to one entry does not silence the signal.
    """
    items = [
        _item("ns_a.tool", "tool", "shared tool description", namespace="ns_a"),
        _item("ns_b.tool", "tool", "shared tool description", namespace="ns_b"),
    ]
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items, top_k=1, confidence_gap=0.5)
    result = router.route("tool")
    assert len(result.candidate_ids) == 1
    assert result.is_ambiguous is True
    assert result.clarifying_question is not None
    assert result.trace.runner_up_score is not None
    assert result.trace.runner_up_score <= result.trace.top_score


# ------------------------------------------------------------------
# EngineRegistry wiring (M-1) and context-boost metadata (M-3)
# ------------------------------------------------------------------


def test_router_uses_supplied_retriever() -> None:
    """A custom :class:`Retriever` supplied via ``retriever=`` is invoked.

    The stub returns descending scores keyed on corpus index so the
    item registered last (highest index) wins.  Confirms the registry
    wiring is end-to-end and not just a held-but-unused reference.
    """

    class _StubRetriever:
        def __init__(self) -> None:
            self.corpus_size = 0
            self.fit_calls = 0
            self.score_calls = 0

        def fit(self, corpus: list[str]) -> None:
            self.corpus_size = len(corpus)
            self.fit_calls += 1

        def search(self, query: str, top_k: int) -> list[tuple[int, float]]:
            _ = query
            scored = [(i, float(i)) for i in range(self.corpus_size)]
            scored.sort(key=lambda x: (-x[1], x[0]))
            return scored[: max(0, top_k)]

        def score_one(self, query: str, index: int) -> float:
            _ = query
            self.score_calls += 1
            if not 0 <= index < self.corpus_size:
                return 0.0
            return float(index)

    items = _build_catalog_items()
    graph = TreeBuilder().build(items)
    stub = _StubRetriever()
    router = Router(graph, items=items, retriever=stub, top_k=3)
    result = router.route("anything")
    assert stub.fit_calls == 1
    assert stub.score_calls > 0
    # corpus is sorted by item id; the last item by id ("search_docs")
    # has the highest stub score because its corpus index is largest.
    assert result.candidate_ids[0] == "send_email"


def test_router_resolves_retriever_from_engine_registry() -> None:
    """When neither retriever nor scorer is supplied, the registry default is used."""
    from contextweaver.routing.registry import EngineRegistry, TfIdfRetriever

    registry = EngineRegistry()
    registry.register("retriever", "tfidf", TfIdfRetriever, default=True)

    items = _build_catalog_items()
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items, engine_registry=registry, top_k=3)
    result = router.route("database read")
    # Behaviour matches the default registry path: TF-IDF still ranks
    # db_read at the top for this query.
    assert "db_read" in result.candidate_ids
    assert result.trace.retriever_engine == "tfidf"


def test_context_hints_surface_on_result_and_trace() -> None:
    """``RouteResult.context_hints`` + ``context_boost_applied`` round-trip via the trace.

    Regression for the issue #116 acceptance criterion: callers can
    introspect whether hints were applied.
    """
    items = [
        _item("send_email", "send_email", "Send notification", tags=["comm"]),
        _item("send_sms", "send_sms", "Send notification", tags=["comm"]),
    ]
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items, top_k=2)

    # No hints -> empty metadata, no boost.
    no_hint = router.route("send notification")
    assert no_hint.context_hints == []
    assert no_hint.context_boost_applied is False
    assert no_hint.trace.extra.get("context_hints") == []
    assert no_hint.trace.extra.get("context_boost_applied") is False

    # Whitespace-only hints are noop'd.
    blank = router.route("send notification", context_hints=["   ", "\t"])
    assert blank.context_hints == []
    assert blank.context_boost_applied is False

    # Real hints land on the result and round-trip through trace.extra.
    with_hints = router.route("send notification", context_hints=["email"])
    assert with_hints.context_hints == ["email"]
    assert with_hints.context_boost_applied is True
    assert with_hints.trace.extra["context_hints"] == ["email"]
    assert with_hints.trace.extra["context_boost_applied"] is True
    # Round-trip through to_dict / from_dict keeps the metadata.
    restored = type(with_hints.trace).from_dict(with_hints.trace.to_dict())
    assert restored.extra["context_hints"] == ["email"]
    assert restored.extra["context_boost_applied"] is True


# ---------------------------------------------------------------------------
# RouteResult.explanation() — issue #226
# ---------------------------------------------------------------------------


def test_route_result_explanation_md_contains_query_and_table() -> None:
    items = _build_catalog_items()
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items, top_k=3)
    result = router.route("read database")

    md = result.explanation()  # default "md"

    assert isinstance(md, str)
    assert "### Routing explanation for query `read database`" in md
    # Top-k table header
    assert "| Rank | Tool id | Score |" in md
    # At least one rank-1 row referencing one of the items
    assert any(f"| 1 | `{item.id}` |" in md for item in items)
    # Either gap text (n>=2 candidates) or top-pick fallback
    assert "Confidence gap" in md or "Top pick" in md


def test_route_result_explanation_dict_versioned_and_shaped() -> None:
    items = _build_catalog_items()
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items, top_k=3)
    result = router.route("write database")

    payload = result.explanation(format="dict")
    assert isinstance(payload, dict)
    assert payload["version"] == 1
    # The trace records the query that was actually scored (may be augmented)
    assert "query" in payload and isinstance(payload["query"], str)
    assert payload["retriever_engine"] in {"tfidf", "bm25", "fuzzy"}
    assert isinstance(payload["candidates"], list)
    assert all({"rank", "id", "score"} <= set(c) for c in payload["candidates"])
    if payload["candidates"]:
        assert payload["candidates"][0]["rank"] == 1
    # Versioned, stable boolean fields
    assert isinstance(payload["is_ambiguous"], bool)
    assert isinstance(payload["context_boost_applied"], bool)


def test_route_result_explanation_deterministic() -> None:
    items = _build_catalog_items()
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items, top_k=3)
    result = router.route("send notification")
    a = result.explanation()
    b = result.explanation()
    assert a == b
    # Determinism extends to the dict form
    assert result.explanation(format="dict") == result.explanation(format="dict")


def test_route_result_explanation_handles_empty() -> None:
    """An empty :class:`RouteResult` should not blow up on explanation()."""
    empty = RouteResult()
    md = empty.explanation()
    assert "No candidates returned" in md
    payload = empty.explanation(format="dict")
    assert payload["candidates"] == []
    assert payload["top"] is None
    assert payload["runner_up"] is None
    assert payload["confidence_gap"] is None


def test_route_result_explanation_surfaces_context_hints() -> None:
    items = [
        _item("send_email", "send_email", "Send notification", tags=["comm"]),
        _item("send_sms", "send_sms", "Send notification", tags=["comm"]),
    ]
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items, top_k=2)

    md = router.route("send notification", context_hints=["email"]).explanation()
    assert "Context hints applied" in md
    assert "email" in md


# ------------------------------------------------------------------
# Pipeline-level public surface (issue #56)
# ------------------------------------------------------------------


def test_router_exposes_pipeline_property() -> None:
    """The Router holds the RoutingPipeline it delegates to (issue #56)."""
    from contextweaver.routing.pipeline import RoutingPipeline

    items = _build_catalog_items()
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items)
    assert isinstance(router.pipeline, RoutingPipeline)
    # Same retriever instance — single source of truth for the fitted corpus.
    assert router.pipeline.retriever is router._retriever  # type: ignore[attr-defined]


def test_router_results_unchanged_after_pipeline_refactor_for_a_handful_of_queries() -> None:
    """Spot-check regression gate: known queries return their known top item."""
    items = _build_catalog_items()
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items, top_k=1)
    assert router.route("read database").candidate_ids[0] == "db_read"
    assert router.route("send email to user").candidate_ids[0] == "send_email"
    assert router.route("search documentation").candidate_ids[0] == "search_docs"


# ------------------------------------------------------------------
# History-aware routing (issue #27)
# ------------------------------------------------------------------


def test_route_without_history_kwarg_is_identical_to_pre_27() -> None:
    """Backward-compat: omitting history= must reproduce the prior shape."""
    items = _build_catalog_items()
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items, top_k=3)
    result = router.route("read database")
    assert result.history_adjustments == {}
    assert "history_adjustments" not in result.trace.extra


def test_route_with_history_deprioritises_called_tool() -> None:
    from contextweaver.routing.history import RouteHistory

    items = _build_catalog_items()
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items, top_k=5)

    # Without history: db_read leads.
    fresh = router.route("read database")
    assert fresh.candidate_ids[0] == "db_read"

    # With history saying db_read already ran: the penalty pushes it down.
    history = RouteHistory(
        called_tool_ids=["db_read"],
        last_result_summary=None,
        repeat_penalty=0.1,
    )
    rerouted = router.route("read database", history=history)
    # db_read must no longer be the top pick after a strong penalty.
    assert rerouted.candidate_ids[0] != "db_read"
    # The score delta is reported on the result.
    assert rerouted.history_adjustments.get("db_read", 0.0) < 0.0


def test_route_history_adjustments_surface_on_trace_extra() -> None:
    from contextweaver.routing.history import RouteHistory

    items = _build_catalog_items()
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items, top_k=5)
    history = RouteHistory(called_tool_ids=["db_read"], repeat_penalty=0.5)
    result = router.route("read database", history=history)
    assert "history_adjustments" in result.trace.extra
    assert result.trace.extra["history_adjustments"] == result.history_adjustments


def test_route_history_with_unsatisfied_depends_on_penalises_dependent_tool() -> None:
    """Phase 2: depends_on referencing an uncalled tool subtracts the penalty."""
    from contextweaver.routing.history import RouteHistory
    from contextweaver.types import SelectableItem

    items = [
        SelectableItem(
            id="auth_login",
            kind="tool",
            name="auth_login",
            description="Authenticate the user",
            tags=["auth"],
        ),
        SelectableItem(
            id="send_email",
            kind="tool",
            name="send_email",
            description="Send email",
            tags=["comm"],
            depends_on=["auth_login"],
        ),
    ]
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items, top_k=2)
    # Empty history — auth_login has not run yet, so send_email should be
    # penalised.  We assert the delta is negative; absolute ranking depends
    # on other terms.
    history = RouteHistory(called_tool_ids=[])
    result = router.route("send the email", history=history)
    assert result.history_adjustments.get("send_email", 0.0) < 0.0


def test_route_history_with_provides_requires_boosts_satisfied_tool() -> None:
    """Phase 2: requires fully satisfied by called tools' provides adds the boost."""
    from contextweaver.routing.history import RouteHistory
    from contextweaver.types import SelectableItem

    items = [
        SelectableItem(
            id="search_contacts",
            kind="tool",
            name="search_contacts",
            description="Search the contacts directory",
            tags=["contacts"],
            provides=["contact_id"],
        ),
        SelectableItem(
            id="send_email",
            kind="tool",
            name="send_email",
            description="Send email to a contact",
            tags=["comm"],
            requires=["contact_id"],
        ),
    ]
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items, top_k=2)
    history = RouteHistory(called_tool_ids=["search_contacts"])
    result = router.route("send email", history=history)
    assert result.history_adjustments.get("send_email", 0.0) > 0.0


def test_route_history_result_summary_boost_threads_through_retriever() -> None:
    """``last_result_summary`` is scored via the router's fitted retriever."""
    from contextweaver.routing.history import RouteHistory

    items = _build_catalog_items()
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items, top_k=5)
    history = RouteHistory(
        called_tool_ids=[],
        last_result_summary="user requested an email to be sent",
        result_boost_weight=1.0,
    )
    result = router.route("notify", history=history)
    # send_email should have a positive delta because the result summary
    # mentions "email".
    assert result.history_adjustments.get("send_email", 0.0) > 0.0


def test_route_history_is_byte_identical_to_no_history_when_history_empty() -> None:
    """An all-default RouteHistory must not move any score."""
    from contextweaver.routing.history import RouteHistory

    items = _build_catalog_items()
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items, top_k=5)
    a = router.route("read database")
    b = router.route("read database", history=RouteHistory())
    assert a.candidate_ids == b.candidate_ids
    assert a.scores == b.scores
    assert b.history_adjustments == {}


# ------------------------------------------------------------------
# Shortlist composition: pinning + diversity quotas (issue #509)
# ------------------------------------------------------------------


def _ns_items() -> list[SelectableItem]:
    return [
        _item("a:1", namespace="a", tags=["x"]),
        _item("a:2", namespace="a", tags=["x"]),
        _item("a:3", namespace="a", tags=["x"]),
        _item("b:1", namespace="b", tags=["x"]),
        _item("esc", namespace="safety", tags=["escalate"]),
    ]


def _ns_router(top_k: int = 3) -> Router:
    items = _ns_items()
    graph = TreeBuilder(max_children=20).build(items)
    return Router(graph, items=items, top_k=top_k)


def test_compose_is_noop_when_unconfigured() -> None:
    router = _ns_router()
    a = router.route("tool")
    b = router.route("tool")
    assert a.candidate_ids == b.candidate_ids
    # Unconfigured composition keeps the result within top_k.
    assert len(a.candidate_ids) <= 3


def test_pin_forces_unranked_item_into_shortlist() -> None:
    router = _ns_router()
    pinned = router.route("tool", pin_ids={"esc"})
    assert "esc" in pinned.candidate_ids
    # Pinned item occupies the first slot and carries a forced 0.0 score.
    assert pinned.candidate_ids[0] == "esc"
    assert pinned.scores[0] == 0.0


def test_pin_keeps_score_for_ranked_item() -> None:
    router = _ns_router()
    base = router.route("tool")
    # An item already in the ranked shortlist keeps its real score when pinned.
    top_ranked = base.candidate_ids[0]
    base_score = base.scores[0]
    pinned = router.route("tool", pin_ids={top_ranked})
    idx = pinned.candidate_ids.index(top_ranked)
    assert pinned.scores[idx] == base_score


def test_pin_is_recorded_in_trace() -> None:
    router = _ns_router()
    result = router.route("tool", pin_ids={"esc"})
    assert result.trace.extra.get("pinned") == ["esc"]


def test_pin_ignores_unknown_ids() -> None:
    router = _ns_router()
    result = router.route("tool", pin_ids={"does-not-exist"})
    assert "does-not-exist" not in result.candidate_ids


def test_namespace_quota_caps_per_namespace() -> None:
    router = _ns_router(top_k=20)
    result = router.route("tool", namespace_quota=1)
    namespaces = [item.namespace for item in result.candidate_items]
    assert namespaces.count("a") <= 1
    assert result.trace.extra.get("namespace_quota") == 1


def test_namespace_quota_below_one_raises() -> None:
    router = _ns_router()
    with pytest.raises(ConfigError):
        router.route("tool", namespace_quota=0)


def test_compose_shortlist_rejects_quota_below_one_direct() -> None:
    # ``compose_shortlist`` is exported and may be called without going through
    # ``Router.route``; it must enforce the documented ``namespace_quota >= 1``
    # invariant itself rather than relying on the router guard (PR #585 review).
    with pytest.raises(ConfigError):
        compose_shortlist([], {}, top_k=3, namespace_quota=0)


def test_pin_bypasses_namespace_quota() -> None:
    router = _ns_router(top_k=20)
    # Quota of 1 for namespace "a", but two "a" items are pinned: both survive.
    result = router.route("tool", pin_ids={"a:1", "a:2"}, namespace_quota=1)
    assert "a:1" in result.candidate_ids
    assert "a:2" in result.candidate_ids


# ------------------------------------------------------------------
# Selection contract wiring on RouteResult (#479 / #515)
# ------------------------------------------------------------------


def test_route_result_selection_schema_uses_candidates() -> None:
    router = _ns_router()
    result = router.route("tool")
    schema = result.selection_schema()
    assert schema["properties"]["tool_id"]["enum"] == result.candidate_ids


def test_route_result_validate_selection_accepts_candidate() -> None:
    router = _ns_router()
    result = router.route("tool")
    out = result.validate_selection(result.candidate_ids[0])
    assert out.status == "accepted"


def test_to_routing_decision_repairs_and_records_selection() -> None:
    router = _ns_router()
    result = router.route("tool")
    chosen = result.candidate_ids[0]
    decision = result.to_routing_decision(selected_item_id=f"  {chosen}  ")
    # Repaired to the canonical id and resolved to the matching card.
    assert decision.selected_item_id == chosen
    assert decision.selected_card_id == chosen
    selection = decision.metadata["contextweaver"]["selection"]
    assert selection["status"] == "repaired"
    assert selection["repair"] == "strip"


def test_to_routing_decision_records_rejected_selection() -> None:
    router = _ns_router()
    result = router.route("tool")
    decision = result.to_routing_decision(selected_item_id="not:a_candidate@1#zzzz")
    assert decision.selected_card_id is None
    assert decision.metadata["contextweaver"]["selection"]["status"] == "rejected"


def test_to_routing_decision_clears_card_id_when_selection_rejected() -> None:
    # A caller-supplied ``selected_card_id`` must be dropped when the
    # accompanying ``selected_item_id`` is rejected, so a rejected selection can
    # never carry a non-null card id (documented contract; PR #585 review).
    router = _ns_router()
    result = router.route("tool")
    decision = result.to_routing_decision(
        selected_item_id="not:a_candidate@1#zzzz",
        selected_card_id=result.candidate_ids[0],
    )
    assert decision.selected_item_id == "not:a_candidate@1#zzzz"
    assert decision.selected_card_id is None
    assert decision.metadata["contextweaver"]["selection"]["status"] == "rejected"

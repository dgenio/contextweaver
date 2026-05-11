"""Tests for contextweaver.routing.registry (issue #47)."""

from __future__ import annotations

import pytest

from contextweaver.exceptions import ConfigError
from contextweaver.protocols import ClusteringEngine, Reranker, Retriever
from contextweaver.routing.registry import (
    EngineRegistry,
    JaccardClusteringEngine,
    NoOpReranker,
    TfIdfRetriever,
    default_registry,
)
from contextweaver.types import SelectableItem


def _item(iid: str, **kw: str) -> SelectableItem:
    return SelectableItem(
        id=iid,
        kind="tool",
        name=kw.get("name", iid),
        description=kw.get("description", "desc"),
        tags=[],
    )


# ------------------------------------------------------------------
# Default implementations conform to protocols
# ------------------------------------------------------------------


def test_tfidf_retriever_conforms_to_retriever_protocol() -> None:
    assert isinstance(TfIdfRetriever(), Retriever)


def test_no_op_reranker_conforms_to_reranker_protocol() -> None:
    assert isinstance(NoOpReranker(), Reranker)


def test_jaccard_clustering_conforms_to_clustering_protocol() -> None:
    assert isinstance(JaccardClusteringEngine(), ClusteringEngine)


# ------------------------------------------------------------------
# TfIdfRetriever behaviour
# ------------------------------------------------------------------


def test_tfidf_retriever_search_returns_top_k() -> None:
    retriever = TfIdfRetriever()
    retriever.fit(["red apple", "green pear", "blue car", "red velvet"])
    hits = retriever.search("red", top_k=2)
    assert len(hits) == 2
    # The two "red" docs should rank above the others.
    ranked_ids = [idx for idx, _ in hits]
    assert 0 in ranked_ids and 3 in ranked_ids


def test_tfidf_retriever_unfit_returns_empty() -> None:
    assert TfIdfRetriever().search("anything", top_k=5) == []


def test_tfidf_retriever_deterministic_ties() -> None:
    """Tied scores break by ascending corpus index."""
    r1 = TfIdfRetriever()
    r2 = TfIdfRetriever()
    corpus = ["foo", "foo", "foo"]
    r1.fit(corpus)
    r2.fit(corpus)
    assert r1.search("foo", top_k=3) == r2.search("foo", top_k=3)


# ------------------------------------------------------------------
# NoOpReranker behaviour
# ------------------------------------------------------------------


def test_no_op_reranker_returns_unchanged() -> None:
    candidates = [("a", 0.9), ("b", 0.5)]
    out = NoOpReranker().rerank("query", candidates)
    assert out == candidates
    # Returned list is independent of the input list.
    out.append(("c", 0.0))
    assert candidates == [("a", 0.9), ("b", 0.5)]


# ------------------------------------------------------------------
# JaccardClusteringEngine behaviour
# ------------------------------------------------------------------


def test_jaccard_clustering_partitions_items() -> None:
    items = [
        _item("a", description="database read"),
        _item("b", description="database write"),
        _item("c", description="email send"),
        _item("d", description="email receive"),
    ]
    clusters = JaccardClusteringEngine().cluster(items, k=2)
    assert sum(len(v) for v in clusters.values()) == 4
    assert len(clusters) <= 2


def test_jaccard_clustering_handles_empty() -> None:
    assert JaccardClusteringEngine().cluster([], k=3) == {}


def test_jaccard_clustering_k_one_returns_single_cluster() -> None:
    items = [_item("a"), _item("b")]
    clusters = JaccardClusteringEngine().cluster(items, k=1)
    assert len(clusters) == 1
    assert sum(len(v) for v in clusters.values()) == 2


# ------------------------------------------------------------------
# EngineRegistry behaviour
# ------------------------------------------------------------------


def test_default_registry_resolves_defaults() -> None:
    assert isinstance(default_registry.resolve("retriever"), TfIdfRetriever)
    assert isinstance(default_registry.resolve("reranker"), NoOpReranker)
    assert isinstance(default_registry.resolve("clustering"), JaccardClusteringEngine)


def test_registry_resolves_by_name() -> None:
    assert isinstance(default_registry.resolve("retriever", "tfidf"), TfIdfRetriever)


def test_resolve_returns_fresh_instance() -> None:
    r1 = default_registry.resolve("retriever")
    r2 = default_registry.resolve("retriever")
    assert r1 is not r2


def test_register_overwrites_existing() -> None:
    registry = EngineRegistry()
    registry.register("retriever", "tfidf", TfIdfRetriever, default=True)
    # Same name, different factory.
    registry.register("retriever", "tfidf", lambda: "stub", default=True)
    assert registry.resolve("retriever") == "stub"


def test_resolve_unknown_slot_raises() -> None:
    registry = EngineRegistry()
    with pytest.raises(ConfigError, match="Unknown engine slot"):
        registry.resolve("unknown")


def test_resolve_unknown_engine_raises() -> None:
    registry = EngineRegistry()
    registry.register("retriever", "tfidf", TfIdfRetriever, default=True)
    with pytest.raises(ConfigError, match="not registered under slot"):
        registry.resolve("retriever", "missing")


def test_resolve_no_default_raises() -> None:
    registry = EngineRegistry()
    with pytest.raises(ConfigError, match="No default engine"):
        registry.resolve("retriever")


def test_register_unknown_slot_raises() -> None:
    registry = EngineRegistry()
    with pytest.raises(ConfigError, match="Unknown engine slot"):
        registry.register("nope", "x", lambda: None)


def test_list_engines_sorted() -> None:
    registry = EngineRegistry()
    registry.register("retriever", "z", lambda: None)
    registry.register("retriever", "a", lambda: None)
    assert registry.list_engines("retriever") == ["a", "z"]


def test_default_for() -> None:
    registry = EngineRegistry()
    assert registry.default_for("retriever") is None
    registry.register("retriever", "tfidf", TfIdfRetriever, default=True)
    assert registry.default_for("retriever") == "tfidf"


def test_first_register_becomes_default() -> None:
    registry = EngineRegistry()
    registry.register("retriever", "first", lambda: "first")
    registry.register("retriever", "second", lambda: "second")
    assert registry.resolve("retriever") == "first"

"""Tests for contextweaver.routing.pipeline (issue #56)."""

from __future__ import annotations

import pytest

from contextweaver.profiles import RoutingConfig
from contextweaver.protocols import (
    CardPacker,
    Navigator,
    Reranker,
    Retriever,
)
from contextweaver.routing.navigator import BeamSearchNavigator
from contextweaver.routing.packer import DefaultCardPacker
from contextweaver.routing.pipeline import RoutingPipeline
from contextweaver.routing.registry import EngineRegistry, TfIdfRetriever
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import SelectableItem


def _item(iid: str, **kw: object) -> SelectableItem:
    return SelectableItem(
        id=iid,
        kind="tool",
        name=str(kw.get("name", iid)),
        description=str(kw.get("description", "desc")),
        tags=list(kw.get("tags", [])),  # type: ignore[arg-type]
        namespace=str(kw.get("namespace", "")),
    )


def _catalog() -> list[SelectableItem]:
    return [
        _item("db_read", name="read_db", description="Read database", tags=["data"]),
        _item("db_write", name="write_db", description="Write database", tags=["data"]),
        _item("send_email", name="send_email", description="Send email", tags=["comm"]),
        _item("search_docs", name="search_docs", description="Search docs", tags=["search"]),
    ]


# ---------------------------------------------------------------------------
# from_config
# ---------------------------------------------------------------------------


def test_from_config_default_pipeline_has_all_stages_wired() -> None:
    """Default pipeline wires retriever + navigator + packer; reranker is None."""
    pipeline = RoutingPipeline.from_config(RoutingConfig())
    assert isinstance(pipeline.retriever, Retriever)
    assert pipeline.reranker is None  # NEW pipeline does not enable rerank by default
    assert isinstance(pipeline.navigator, Navigator)
    assert isinstance(pipeline.packer, CardPacker)


def test_from_config_propagates_routing_config_to_navigator() -> None:
    """beam_width / max_depth / top_k / confidence_gap flow into the navigator."""
    cfg = RoutingConfig(beam_width=5, max_depth=12, top_k=7, confidence_gap=0.25)
    pipeline = RoutingPipeline.from_config(cfg)
    assert isinstance(pipeline.navigator, BeamSearchNavigator)
    nav = pipeline.navigator
    # Internal fields are private but stable; assert via repr / vars to pin
    assert nav._beam_width == 5
    assert nav._max_depth == 12
    assert nav._top_k == 7
    assert nav._confidence_gap == 0.25


def test_from_config_uses_default_registry_when_none() -> None:
    pipeline = RoutingPipeline.from_config()
    assert isinstance(pipeline.retriever, Retriever)


def test_from_config_respects_custom_registry() -> None:
    """A registry with a non-default retriever is honoured."""

    class _ConstantRetriever:
        def fit(self, corpus: list[str]) -> None:
            self._n = len(corpus)

        def search(self, query: str, top_k: int) -> list[tuple[int, float]]:
            return [(i, 0.5) for i in range(min(top_k, self._n))]

        def score_one(self, query: str, index: int) -> float:
            return 0.5

    reg = EngineRegistry()
    reg.register("retriever", "constant", _ConstantRetriever, default=True)
    pipeline = RoutingPipeline.from_config(registry=reg)
    assert type(pipeline.retriever).__name__ == "_ConstantRetriever"


# ---------------------------------------------------------------------------
# Direct construction
# ---------------------------------------------------------------------------


def test_direct_construction_accepts_only_retriever() -> None:
    """Pipeline can be constructed with only a retriever; other slots are None / default."""
    pipeline = RoutingPipeline(retriever=TfIdfRetriever())
    assert pipeline.reranker is None
    assert pipeline.navigator is None
    assert isinstance(pipeline.packer, DefaultCardPacker)


def test_direct_construction_accepts_explicit_reranker() -> None:
    """An explicit reranker survives construction unchanged."""

    class _IdReranker:
        def rerank(
            self,
            query: str,
            candidates: list[tuple[str, float]],
        ) -> list[tuple[str, float]]:
            return list(candidates)

    pipeline = RoutingPipeline(retriever=TfIdfRetriever(), reranker=_IdReranker())
    assert isinstance(pipeline.reranker, Reranker)
    assert type(pipeline.reranker).__name__ == "_IdReranker"


# ---------------------------------------------------------------------------
# Stage-level entry points
# ---------------------------------------------------------------------------


def test_navigate_in_isolation_returns_navigation_result() -> None:
    items = _catalog()
    graph = TreeBuilder().build(items)
    retriever = TfIdfRetriever()
    docs = [f"{it.name} {it.description} {' '.join(it.tags)}" for it in items]
    retriever.fit(docs)
    doc_id_to_idx = {it.id: i for i, it in enumerate(items)}
    pipeline = RoutingPipeline(
        retriever=retriever,
        navigator=BeamSearchNavigator(beam_width=3, max_depth=5, top_k=10),
    )
    result = pipeline.navigate(
        "read database",
        graph,
        {it.id: it for it in items},
        doc_id_to_idx,
        all_item_ids={it.id for it in items},
    )
    assert "db_read" in result.collected
    assert result.steps == []  # debug=False


def test_navigate_with_debug_populates_steps() -> None:
    items = _catalog()
    graph = TreeBuilder().build(items)
    retriever = TfIdfRetriever()
    docs = [f"{it.name} {it.description} {' '.join(it.tags)}" for it in items]
    retriever.fit(docs)
    doc_id_to_idx = {it.id: i for i, it in enumerate(items)}
    pipeline = RoutingPipeline.from_config(RoutingConfig(beam_width=3, max_depth=5))
    pipeline = RoutingPipeline(retriever=retriever, navigator=pipeline.navigator)
    result = pipeline.navigate(
        "read database",
        graph,
        {it.id: it for it in items},
        doc_id_to_idx,
        all_item_ids={it.id for it in items},
        debug=True,
    )
    assert result.steps  # at least one step recorded


def test_rerank_with_no_reranker_is_passthrough() -> None:
    pipeline = RoutingPipeline(retriever=TfIdfRetriever())
    pairs = [("a", 0.9), ("b", 0.5), ("c", 0.1)]
    assert pipeline.rerank("any query", pairs) == pairs


def test_rerank_calls_reranker_when_present() -> None:
    """Reranker.rerank() is invoked; result threads through verbatim."""

    class _ReverseReranker:
        def rerank(
            self,
            query: str,
            candidates: list[tuple[str, float]],
        ) -> list[tuple[str, float]]:
            return list(reversed(candidates))

    pipeline = RoutingPipeline(retriever=TfIdfRetriever(), reranker=_ReverseReranker())
    pairs = [("a", 0.9), ("b", 0.5)]
    assert pipeline.rerank("q", pairs) == [("b", 0.5), ("a", 0.9)]


def test_pack_renders_choice_cards() -> None:
    items = _catalog()
    pipeline = RoutingPipeline(retriever=TfIdfRetriever())
    scores = {it.id: 1.0 - 0.1 * i for i, it in enumerate(items)}
    cards = pipeline.pack(items, scores)
    assert len(cards) == len(items)
    # Score-descending order with id tie-break — established invariant (#218)
    assert cards[0].id == items[0].id  # highest score by construction
    assert cards[0].score is not None
    assert cards[0].score >= (cards[-1].score or -1.0)


def test_pack_respects_budget_tokens_soft_cap() -> None:
    items = _catalog()
    pipeline = RoutingPipeline(retriever=TfIdfRetriever())
    scores = {it.id: 1.0 for it in items}
    # Forcing a tiny budget — at least one card must always come back so the
    # pipeline never returns an empty list when items exist.
    cards = pipeline.pack(items, scores, budget_tokens=1)
    assert cards
    assert len(cards) < len(items)


# ---------------------------------------------------------------------------
# Defensive: invalid construction
# ---------------------------------------------------------------------------


def test_pipeline_retriever_field_is_required() -> None:
    """Retriever has no default and must be supplied."""
    with pytest.raises(TypeError):
        RoutingPipeline()  # type: ignore[call-arg]

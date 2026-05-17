"""Tests for contextweaver.routing.navigator (issue #56)."""

from __future__ import annotations

from contextweaver.routing.navigator import BeamSearchNavigator, rank_collected
from contextweaver.routing.registry import TfIdfRetriever
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


def _setup() -> tuple[
    list[SelectableItem],
    object,
    TfIdfRetriever,
    dict[str, int],
]:
    items = [
        _item("db_read", name="read_db", description="Read database", tags=["data"]),
        _item("db_write", name="write_db", description="Write database", tags=["data"]),
        _item("send_email", name="send_email", description="Send email", tags=["comm"]),
        _item("search_docs", name="search_docs", description="Search docs", tags=["search"]),
    ]
    graph = TreeBuilder().build(items)
    retriever = TfIdfRetriever()
    docs: list[str] = []
    doc_ids: list[str] = []
    for it in sorted(items, key=lambda x: x.id):
        docs.append(f"{it.name} {it.description} {' '.join(it.tags)}")
        doc_ids.append(it.id)
    for nid in graph.nodes():
        if nid not in {it.id for it in items}:
            node = graph.get_node(nid)
            docs.append(f"{node.label} {node.routing_hint}")
            doc_ids.append(nid)
    retriever.fit(docs)
    doc_id_to_idx = {did: i for i, did in enumerate(doc_ids)}
    return items, graph, retriever, doc_id_to_idx


def test_navigate_returns_navigation_result_with_collected_items() -> None:
    items, graph, retriever, doc_id_to_idx = _setup()
    nav = BeamSearchNavigator(beam_width=3, max_depth=5, top_k=10)
    result = nav.navigate(
        "read database",
        graph,  # type: ignore[arg-type]
        {it.id: it for it in items},
        retriever,
        doc_id_to_idx,
        all_item_ids={it.id for it in items},
    )
    assert "db_read" in result.collected
    score, path = result.collected["db_read"]
    assert isinstance(score, float)
    assert isinstance(path, list)


def test_navigate_is_deterministic_across_calls() -> None:
    items, graph, retriever, doc_id_to_idx = _setup()
    nav = BeamSearchNavigator(beam_width=3, max_depth=5, top_k=10)
    a = nav.navigate(
        "read database",
        graph,  # type: ignore[arg-type]
        {it.id: it for it in items},
        retriever,
        doc_id_to_idx,
        all_item_ids={it.id for it in items},
    )
    b = nav.navigate(
        "read database",
        graph,  # type: ignore[arg-type]
        {it.id: it for it in items},
        retriever,
        doc_id_to_idx,
        all_item_ids={it.id for it in items},
    )
    assert a.collected == b.collected


def test_navigate_debug_populates_steps_otherwise_empty() -> None:
    items, graph, retriever, doc_id_to_idx = _setup()
    nav = BeamSearchNavigator(beam_width=3, max_depth=5, top_k=10)
    quiet = nav.navigate(
        "read",
        graph,  # type: ignore[arg-type]
        {it.id: it for it in items},
        retriever,
        doc_id_to_idx,
        all_item_ids={it.id for it in items},
    )
    loud = nav.navigate(
        "read",
        graph,  # type: ignore[arg-type]
        {it.id: it for it in items},
        retriever,
        doc_id_to_idx,
        all_item_ids={it.id for it in items},
        debug=True,
    )
    assert quiet.steps == []
    assert loud.steps
    # Identical search result either way; debug only affects the trace
    assert quiet.collected == loud.collected


def test_navigate_skips_inactive_items() -> None:
    """Items removed from active_items must not appear in the collected map."""
    items, graph, retriever, doc_id_to_idx = _setup()
    active = {it.id: it for it in items if it.id != "db_read"}
    nav = BeamSearchNavigator(beam_width=3, max_depth=5, top_k=10)
    result = nav.navigate(
        "read database",
        graph,  # type: ignore[arg-type]
        active,
        retriever,
        doc_id_to_idx,
        all_item_ids={it.id for it in items},
    )
    assert "db_read" not in result.collected


def test_rank_collected_sorts_by_negative_score_then_id() -> None:
    active = {
        "a": _item("a"),
        "b": _item("b"),
        "c": _item("c"),
    }
    collected = {
        "a": (0.3, ["root", "a"]),
        "b": (0.9, ["root", "b"]),
        "c": (0.9, ["root", "c"]),  # tie with b → b before c
    }
    ranked = rank_collected(collected, active)
    assert [iid for iid, _ in ranked] == ["b", "c", "a"]


def test_rank_collected_drops_inactive_entries() -> None:
    active = {"a": _item("a")}
    collected = {
        "a": (0.5, ["root", "a"]),
        "ghost": (0.9, ["root", "ghost"]),
    }
    ranked = rank_collected(collected, active)
    assert [iid for iid, _ in ranked] == ["a"]

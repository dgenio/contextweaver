"""Tests for contextweaver.store.event_log."""

from __future__ import annotations

import pytest

from contextweaver.exceptions import DuplicateItemError, ItemNotFoundError
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.types import ContextItem, ItemKind


def _make_item(iid: str, kind: ItemKind = ItemKind.user_turn, text: str = "text") -> ContextItem:
    return ContextItem(id=iid, kind=kind, text=text)


def test_append_and_get() -> None:
    log = InMemoryEventLog()
    item = _make_item("i1")
    log.append(item)
    assert log.get("i1").text == "text"


def test_duplicate_raises() -> None:
    log = InMemoryEventLog()
    log.append(_make_item("i1"))
    with pytest.raises(DuplicateItemError):
        log.append(_make_item("i1"))


def test_get_missing_raises() -> None:
    log = InMemoryEventLog()
    with pytest.raises(ItemNotFoundError):
        log.get("missing")


def test_all_returns_insertion_order() -> None:
    log = InMemoryEventLog()
    for i in range(5):
        log.append(_make_item(f"i{i}"))
    assert [item.id for item in log.all()] == [f"i{i}" for i in range(5)]


def test_filter_by_kind() -> None:
    log = InMemoryEventLog()
    log.append(_make_item("u1", ItemKind.user_turn))
    log.append(_make_item("t1", ItemKind.tool_call))
    log.append(_make_item("u2", ItemKind.user_turn))
    results = log.filter_by_kind(ItemKind.user_turn)
    assert [r.id for r in results] == ["u1", "u2"]


def test_tail() -> None:
    log = InMemoryEventLog()
    for i in range(10):
        log.append(_make_item(f"i{i}"))
    tail = log.tail(3)
    assert len(tail) == 3
    assert tail[-1].id == "i9"


def test_len() -> None:
    log = InMemoryEventLog()
    assert len(log) == 0
    log.append(_make_item("i1"))
    assert len(log) == 1


def test_roundtrip() -> None:
    log = InMemoryEventLog()
    log.append(_make_item("i1", ItemKind.agent_msg, "hello"))
    restored = InMemoryEventLog.from_dict(log.to_dict())
    assert len(restored) == 1
    assert restored.get("i1").text == "hello"


def test_query_no_filters() -> None:
    log = InMemoryEventLog()
    for i in range(5):
        log.append(_make_item(f"i{i}"))
    assert len(log.query()) == 5


def test_query_with_kinds() -> None:
    log = InMemoryEventLog()
    log.append(_make_item("u1", ItemKind.user_turn))
    log.append(_make_item("t1", ItemKind.tool_call))
    log.append(_make_item("u2", ItemKind.user_turn))
    results = log.query(kinds=[ItemKind.user_turn])
    assert len(results) == 2


def test_query_with_since() -> None:
    log = InMemoryEventLog()
    for i in range(5):
        log.append(_make_item(f"i{i}"))
    results = log.query(since=3)
    assert len(results) == 2
    assert results[0].id == "i3"


def test_query_with_limit() -> None:
    log = InMemoryEventLog()
    for i in range(5):
        log.append(_make_item(f"i{i}"))
    results = log.query(limit=2)
    assert len(results) == 2


def test_query_combined_filters() -> None:
    log = InMemoryEventLog()
    log.append(_make_item("u1", ItemKind.user_turn))
    log.append(_make_item("t1", ItemKind.tool_call))
    log.append(_make_item("u2", ItemKind.user_turn))
    log.append(_make_item("t2", ItemKind.tool_call))
    log.append(_make_item("u3", ItemKind.user_turn))
    results = log.query(kinds=[ItemKind.user_turn], since=2, limit=1)
    assert len(results) == 1
    assert results[0].id == "u2"


def test_children() -> None:
    log = InMemoryEventLog()
    log.append(_make_item("parent1"))
    log.append(ContextItem(id="child1", kind=ItemKind.tool_result, text="r1", parent_id="parent1"))
    log.append(ContextItem(id="child2", kind=ItemKind.tool_result, text="r2", parent_id="parent1"))
    log.append(_make_item("other"))
    children = log.children("parent1")
    assert len(children) == 2
    assert {c.id for c in children} == {"child1", "child2"}


def test_children_empty() -> None:
    log = InMemoryEventLog()
    log.append(_make_item("i1"))
    assert log.children("i1") == []


def test_parent_found() -> None:
    log = InMemoryEventLog()
    log.append(_make_item("p1"))
    log.append(ContextItem(id="c1", kind=ItemKind.tool_result, text="r", parent_id="p1"))
    parent = log.parent("c1")
    assert parent is not None
    assert parent.id == "p1"


def test_parent_none() -> None:
    log = InMemoryEventLog()
    log.append(_make_item("i1"))
    assert log.parent("i1") is None


def test_parent_missing_parent_id() -> None:
    log = InMemoryEventLog()
    log.append(ContextItem(id="c1", kind=ItemKind.tool_result, text="r", parent_id="missing"))
    assert log.parent("c1") is None


def test_parent_not_found_raises() -> None:
    log = InMemoryEventLog()
    with pytest.raises(ItemNotFoundError):
        log.parent("missing")


def test_count() -> None:
    log = InMemoryEventLog()
    assert log.count() == 0
    log.append(_make_item("i1"))
    log.append(_make_item("i2"))
    assert log.count() == 2

"""Tests for contextweaver.store.event_log."""

from __future__ import annotations

import pytest

from contextweaver.exceptions import ItemNotFoundError
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
    with pytest.raises(ValueError):
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


# -- new methods: query, children, parent, count ----------------------------


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
    assert [r.id for r in results] == ["u1", "u2"]


def test_query_with_since() -> None:
    log = InMemoryEventLog()
    for i in range(5):
        log.append(_make_item(f"i{i}"))
    results = log.query(since=3)
    assert [r.id for r in results] == ["i3", "i4"]


def test_query_with_limit() -> None:
    log = InMemoryEventLog()
    for i in range(10):
        log.append(_make_item(f"i{i}"))
    results = log.query(limit=3)
    assert len(results) == 3


def test_children() -> None:
    log = InMemoryEventLog()
    log.append(_make_item("p1"))
    child = ContextItem(id="c1", kind=ItemKind.tool_result, text="r", parent_id="p1")
    log.append(child)
    children = log.children("p1")
    assert len(children) == 1
    assert children[0].id == "c1"


def test_children_empty() -> None:
    log = InMemoryEventLog()
    log.append(_make_item("p1"))
    assert log.children("p1") == []


def test_parent() -> None:
    log = InMemoryEventLog()
    log.append(_make_item("p1"))
    child = ContextItem(id="c1", kind=ItemKind.tool_result, text="r", parent_id="p1")
    log.append(child)
    parent = log.parent("c1")
    assert parent is not None
    assert parent.id == "p1"


def test_parent_none_when_no_parent() -> None:
    log = InMemoryEventLog()
    log.append(_make_item("i1"))
    assert log.parent("i1") is None


def test_parent_none_when_missing() -> None:
    log = InMemoryEventLog()
    assert log.parent("missing") is None


def test_count() -> None:
    log = InMemoryEventLog()
    assert log.count() == 0
    log.append(_make_item("i1"))
    assert log.count() == 1

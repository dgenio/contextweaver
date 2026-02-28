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

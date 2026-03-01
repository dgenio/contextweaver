"""Tests for contextweaver.store.facts."""

from __future__ import annotations

import pytest

from contextweaver.exceptions import ItemNotFoundError
from contextweaver.store.facts import Fact, InMemoryFactStore


def test_put_and_get() -> None:
    store = InMemoryFactStore()
    fact = Fact(fact_id="f1", key="user_id", value="42")
    store.put(fact)
    assert store.get("f1").value == "42"


def test_get_missing_raises() -> None:
    store = InMemoryFactStore()
    with pytest.raises(ItemNotFoundError):
        store.get("missing")


def test_put_overwrites() -> None:
    store = InMemoryFactStore()
    store.put(Fact("f1", "k", "v1"))
    store.put(Fact("f1", "k", "v2"))
    assert store.get("f1").value == "v2"


def test_get_by_key() -> None:
    store = InMemoryFactStore()
    store.put(Fact("f1", "color", "blue"))
    store.put(Fact("f2", "color", "red"))
    store.put(Fact("f3", "size", "large"))
    results = store.get_by_key("color")
    assert {f.fact_id for f in results} == {"f1", "f2"}


def test_delete() -> None:
    store = InMemoryFactStore()
    store.put(Fact("f1", "k", "v"))
    store.delete("f1")
    with pytest.raises(ItemNotFoundError):
        store.get("f1")


def test_delete_missing_raises() -> None:
    store = InMemoryFactStore()
    with pytest.raises(ItemNotFoundError):
        store.delete("missing")


def test_all_sorted() -> None:
    store = InMemoryFactStore()
    store.put(Fact("z1", "k", "v"))
    store.put(Fact("a1", "k", "v"))
    ids = [f.fact_id for f in store.all()]
    assert ids == ["a1", "z1"]


def test_roundtrip() -> None:
    store = InMemoryFactStore()
    store.put(Fact("f1", "name", "Alice"))
    restored = InMemoryFactStore.from_dict(store.to_dict())
    assert restored.get("f1").value == "Alice"

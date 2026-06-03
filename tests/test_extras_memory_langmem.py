"""Tests for contextweaver.extras.memory.langmem (issue #195).

The adapter wraps a LangGraph ``BaseStore``.  Functional tests run against a
real ``langgraph.store.memory.InMemoryStore`` (no mocking needed) when
``langgraph`` is importable; one import-error test always runs to cover the
missing-extra branch.  Note that an un-indexed ``InMemoryStore`` ignores the
``query`` and returns every item in the namespace — so ``search`` assertions
check membership and namespace isolation, not ranking.
"""

from __future__ import annotations

import importlib

import pytest

from contextweaver.exceptions import ItemNotFoundError


def _langmem_available() -> bool:
    try:
        importlib.import_module("langgraph.store.base")
    except ImportError:
        return False
    return True


HAS_LANGMEM = _langmem_available()


def test_import_error_message_when_extra_missing() -> None:
    """If ``langgraph`` is missing, importing the adapter must guide the user."""
    if HAS_LANGMEM:
        pytest.skip("langgraph is installed; ImportError path not exercised here")
    with pytest.raises(ImportError, match=r"\[langmem\]"):
        importlib.import_module("contextweaver.extras.memory.langmem")


if HAS_LANGMEM:  # pragma: no branch
    from langgraph.store.memory import InMemoryStore

    from contextweaver.extras.memory.langmem import (
        LangMemBackendError,
        LangMemEpisodicStore,
        LangMemFactStore,
    )
    from contextweaver.store.episodic import Episode
    from contextweaver.store.facts import Fact


@pytest.fixture()
def store() -> object:
    if not HAS_LANGMEM:
        pytest.skip("langgraph not installed")
    return InMemoryStore()


# ----- LangMemEpisodicStore -----


def test_episodic_add_get_roundtrip(store: object) -> None:
    s = LangMemEpisodicStore(store, namespace=("agent", "alice"))
    s.add(Episode("ep-1", "checked logs", tags=["rca"], metadata={"sev": "high"}))
    got = s.get("ep-1")
    assert got is not None
    assert (got.episode_id, got.summary, got.tags, got.metadata) == (
        "ep-1",
        "checked logs",
        ["rca"],
        {"sev": "high"},
    )


def test_episodic_get_missing_returns_none(store: object) -> None:
    assert LangMemEpisodicStore(store).get("nope") is None


def test_episodic_search_is_namespace_scoped(store: object) -> None:
    a = LangMemEpisodicStore(store, namespace=("alice",))
    b = LangMemEpisodicStore(store, namespace=("bob",))
    a.add(Episode("a1", "alice investigated database outage"))
    b.add(Episode("b1", "bob updated database schema"))
    assert [ep.episode_id for ep in a.search("database", top_k=5)] == ["a1"]


def test_episodic_all_and_latest_ordering(store: object) -> None:
    s = LangMemEpisodicStore(store, namespace=("alice",))
    s.add(Episode("a1", "first"))
    s.add(Episode("a2", "second"))
    s.add(Episode("a3", "third"))
    assert [ep.episode_id for ep in s.all()] == ["a1", "a2", "a3"]
    assert [t[0] for t in s.latest(n=2)] == ["a3", "a2"]
    assert s.latest(n=0) == []


def test_episodic_delete_and_missing(store: object) -> None:
    s = LangMemEpisodicStore(store, namespace=("alice",))
    s.add(Episode("a1", "first"))
    s.delete("a1")
    assert s.get("a1") is None
    with pytest.raises(ItemNotFoundError, match="gone"):
        s.delete("gone")


def test_episodic_requires_namespace(store: object) -> None:
    with pytest.raises(LangMemBackendError, match="namespace"):
        LangMemEpisodicStore(store, namespace=())


def test_episodic_scan_limit_raises(store: object) -> None:
    s = LangMemEpisodicStore(store, namespace=("alice",), scan_limit=2)
    s.add(Episode("a1", "x"))
    s.add(Episode("a2", "y"))
    with pytest.raises(NotImplementedError, match="enumeration is no longer complete"):
        s.all()


# ----- LangMemFactStore -----


def test_fact_put_get_and_overwrite(store: object) -> None:
    s = LangMemFactStore(store, namespace=("alice",))
    s.put(Fact("f1", "user.role", "admin"))
    assert s.get("f1").value == "admin"
    s.put(Fact("f1", "user.role", "superadmin"))
    assert s.get("f1").value == "superadmin"


def test_fact_get_missing_raises(store: object) -> None:
    with pytest.raises(ItemNotFoundError, match="nope"):
        LangMemFactStore(store, namespace=("alice",)).get("nope")


def test_fact_get_by_key_sorted(store: object) -> None:
    s = LangMemFactStore(store, namespace=("alice",))
    s.put(Fact("f2", "tags.color", "blue"))
    s.put(Fact("f1", "tags.color", "green"))
    assert [f.fact_id for f in s.get_by_key("tags.color")] == ["f1", "f2"]


def test_fact_list_keys_with_prefix(store: object) -> None:
    s = LangMemFactStore(store, namespace=("alice",))
    s.put(Fact("f1", "user.role", "admin"))
    s.put(Fact("f2", "user.email", "a@b"))
    s.put(Fact("f3", "system.region", "eu"))
    assert s.list_keys(prefix="user.") == ["user.email", "user.role"]
    assert s.list_keys() == ["system.region", "user.email", "user.role"]


def test_fact_delete_and_all(store: object) -> None:
    s = LangMemFactStore(store, namespace=("alice",))
    s.put(Fact("f2", "k1", "v1"))
    s.put(Fact("f1", "k2", "v2"))
    assert [f.fact_id for f in s.all()] == ["f1", "f2"]
    s.delete("f1")
    with pytest.raises(ItemNotFoundError):
        s.get("f1")

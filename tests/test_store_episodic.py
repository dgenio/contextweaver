"""Tests for contextweaver.store.episodic."""

from __future__ import annotations

import pytest

from contextweaver.exceptions import ItemNotFoundError
from contextweaver.store.episodic import Episode, InMemoryEpisodicStore


def test_add_and_get() -> None:
    store = InMemoryEpisodicStore()
    ep = Episode(episode_id="e1", summary="Search the database for user records")
    store.add(ep)
    result = store.get("e1")
    assert result is not None
    assert result.summary == ep.summary


def test_get_missing_returns_none() -> None:
    store = InMemoryEpisodicStore()
    assert store.get("missing") is None


def test_search_returns_relevant() -> None:
    store = InMemoryEpisodicStore()
    store.add(Episode("e1", "search database records quickly"))
    store.add(Episode("e2", "send notification email"))
    store.add(Episode("e3", "compute statistics from database"))
    results = store.search("database search", top_k=2)
    ids = [r.episode_id for r in results]
    assert "e1" in ids


def test_search_empty_store() -> None:
    store = InMemoryEpisodicStore()
    assert store.search("anything") == []


def test_all() -> None:
    store = InMemoryEpisodicStore()
    store.add(Episode("e1", "first"))
    store.add(Episode("e2", "second"))
    all_eps = store.all()
    assert len(all_eps) == 2


def test_roundtrip() -> None:
    store = InMemoryEpisodicStore()
    store.add(Episode("e1", "summary one", tags=["a"]))
    restored = InMemoryEpisodicStore.from_dict(store.to_dict())
    ep = restored.get("e1")
    assert ep is not None
    assert ep.tags == ["a"]


def test_latest_returns_recent_first() -> None:
    store = InMemoryEpisodicStore()
    store.add(Episode("e1", "first", metadata={"idx": 1}))
    store.add(Episode("e2", "second", metadata={"idx": 2}))
    store.add(Episode("e3", "third", metadata={"idx": 3}))
    result = store.latest(2)
    assert len(result) == 2
    # Most recent first
    assert result[0][0] == "e3"
    assert result[1][0] == "e2"


def test_latest_more_than_available() -> None:
    store = InMemoryEpisodicStore()
    store.add(Episode("e1", "only"))
    result = store.latest(5)
    assert len(result) == 1
    assert result[0][0] == "e1"


def test_latest_zero() -> None:
    store = InMemoryEpisodicStore()
    store.add(Episode("e1", "something"))
    assert store.latest(0) == []


def test_delete_existing() -> None:
    store = InMemoryEpisodicStore()
    store.add(Episode("e1", "to delete"))
    store.add(Episode("e2", "to keep"))
    store.delete("e1")
    assert store.get("e1") is None
    assert store.get("e2") is not None
    assert len(store.all()) == 1


def test_delete_missing_raises() -> None:
    store = InMemoryEpisodicStore()
    with pytest.raises(ItemNotFoundError):
        store.delete("missing")


def test_episode_roundtrip() -> None:
    ep = Episode(
        episode_id="ep1",
        summary="found 3 records",
        tags=["search", "db"],
        metadata={"took_ms": 42},
    )
    d = ep.to_dict()
    restored = Episode.from_dict(d)
    assert restored.episode_id == "ep1"
    assert restored.tags == ["search", "db"]
    assert restored.metadata["took_ms"] == 42

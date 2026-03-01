"""Tests for contextweaver.store.episodic."""

from __future__ import annotations

import pytest

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


# -- new methods: latest, delete, list_episodes ------------------------------


def test_latest_default() -> None:
    store = InMemoryEpisodicStore()
    store.add(Episode("e1", "first"))
    store.add(Episode("e2", "second"))
    store.add(Episode("e3", "third"))
    store.add(Episode("e4", "fourth"))
    results = store.latest()  # default n=3
    assert len(results) == 3
    # Most recent first
    assert results[0][0] == "e4"
    assert results[1][0] == "e3"
    assert results[2][0] == "e2"


def test_latest_custom_n() -> None:
    store = InMemoryEpisodicStore()
    store.add(Episode("e1", "first"))
    store.add(Episode("e2", "second"))
    results = store.latest(n=1)
    assert len(results) == 1
    assert results[0][0] == "e2"


def test_latest_returns_tuples() -> None:
    store = InMemoryEpisodicStore()
    store.add(Episode("e1", "summary", metadata={"k": "v"}))
    results = store.latest(n=1)
    eid, summary, meta = results[0]
    assert eid == "e1"
    assert summary == "summary"
    assert meta == {"k": "v"}


def test_list_episodes_unlimited() -> None:
    store = InMemoryEpisodicStore()
    store.add(Episode("e1", "first"))
    store.add(Episode("e2", "second"))
    episodes = store.list_episodes()
    assert len(episodes) == 2
    assert episodes[0].episode_id == "e1"


def test_list_episodes_with_limit() -> None:
    store = InMemoryEpisodicStore()
    for i in range(5):
        store.add(Episode(f"e{i}", f"summary {i}"))
    episodes = store.list_episodes(limit=2)
    assert len(episodes) == 2


def test_delete() -> None:
    store = InMemoryEpisodicStore()
    store.add(Episode("e1", "first"))
    store.add(Episode("e2", "second"))
    store.delete("e1")
    assert store.get("e1") is None
    assert len(store.all()) == 1


def test_delete_missing_raises() -> None:
    from contextweaver.exceptions import ItemNotFoundError

    store = InMemoryEpisodicStore()
    with pytest.raises(ItemNotFoundError):
        store.delete("missing")

"""Tests for contextweaver.store.episodic."""

from __future__ import annotations

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

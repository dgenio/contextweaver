"""Tests for contextweaver.store.episodic -- async put/get/latest/list/delete."""

from __future__ import annotations

import pytest

from contextweaver.store.episodic import InMemoryEpisodicStore


class TestInMemoryEpisodicStore:
    """Tests for InMemoryEpisodicStore async methods."""

    async def test_put_and_get(self, episodic_store: InMemoryEpisodicStore) -> None:
        await episodic_store.put("ep1", "User searched for invoices")
        summary, meta = await episodic_store.get("ep1")
        assert summary == "User searched for invoices"
        assert meta == {}

    async def test_put_with_metadata(self, episodic_store: InMemoryEpisodicStore) -> None:
        await episodic_store.put("ep1", "summary", metadata={"score": 0.9})
        _, meta = await episodic_store.get("ep1")
        assert meta["score"] == 0.9

    async def test_get_missing_raises(self, episodic_store: InMemoryEpisodicStore) -> None:
        with pytest.raises(KeyError):
            await episodic_store.get("missing")

    async def test_list_episodes(self, episodic_store: InMemoryEpisodicStore) -> None:
        await episodic_store.put("ep1", "first")
        await episodic_store.put("ep2", "second")
        await episodic_store.put("ep3", "third")
        ids = await episodic_store.list_episodes()
        assert ids == ["ep1", "ep2", "ep3"]

    async def test_list_episodes_with_limit(self, episodic_store: InMemoryEpisodicStore) -> None:
        for i in range(5):
            await episodic_store.put(f"ep{i}", f"summary {i}")
        ids = await episodic_store.list_episodes(limit=2)
        assert len(ids) == 2

    async def test_latest(self, episodic_store: InMemoryEpisodicStore) -> None:
        await episodic_store.put("ep1", "first")
        await episodic_store.put("ep2", "second")
        await episodic_store.put("ep3", "third")
        latest = await episodic_store.latest(n=2)
        assert len(latest) == 2
        # Most recent first
        assert latest[0][0] == "ep3"
        assert latest[1][0] == "ep2"

    async def test_delete(self, episodic_store: InMemoryEpisodicStore) -> None:
        await episodic_store.put("ep1", "to delete")
        await episodic_store.delete("ep1")
        with pytest.raises(KeyError):
            await episodic_store.get("ep1")

    async def test_delete_missing_raises(self, episodic_store: InMemoryEpisodicStore) -> None:
        with pytest.raises(KeyError):
            await episodic_store.delete("missing")

    async def test_put_update_preserves_order(self, episodic_store: InMemoryEpisodicStore) -> None:
        await episodic_store.put("ep1", "original")
        await episodic_store.put("ep2", "second")
        await episodic_store.put("ep1", "updated")
        ids = await episodic_store.list_episodes()
        # ep1 should stay in its original position
        assert ids == ["ep1", "ep2"]
        summary, _ = await episodic_store.get("ep1")
        assert summary == "updated"

    async def test_roundtrip(self, episodic_store: InMemoryEpisodicStore) -> None:
        await episodic_store.put("ep1", "summary one", metadata={"tag": "a"})
        data = episodic_store.to_dict()
        restored = InMemoryEpisodicStore.from_dict(data)
        summary, meta = await restored.get("ep1")
        assert summary == "summary one"
        assert meta["tag"] == "a"

"""Tests for contextweaver.store.facts -- async put/get/get_all/list_keys/delete."""

from __future__ import annotations

import pytest

from contextweaver.store.facts import InMemoryFactStore


class TestInMemoryFactStore:
    """Tests for InMemoryFactStore async methods."""

    async def test_put_and_get(self, fact_store: InMemoryFactStore) -> None:
        await fact_store.put("user_name", "Alice")
        result = await fact_store.get("user_name")
        assert result is not None
        assert result[0] == "Alice"
        assert result[1] == {}

    async def test_put_with_metadata(self, fact_store: InMemoryFactStore) -> None:
        await fact_store.put("user_name", "Alice", metadata={"source": "login"})
        result = await fact_store.get("user_name")
        assert result is not None
        assert result[1]["source"] == "login"

    async def test_get_missing_returns_none(self, fact_store: InMemoryFactStore) -> None:
        result = await fact_store.get("missing")
        assert result is None

    async def test_put_overwrites(self, fact_store: InMemoryFactStore) -> None:
        await fact_store.put("key", "value1")
        await fact_store.put("key", "value2")
        result = await fact_store.get("key")
        assert result is not None
        assert result[0] == "value2"

    async def test_list_keys(self, fact_store: InMemoryFactStore) -> None:
        await fact_store.put("b_key", "val")
        await fact_store.put("a_key", "val")
        await fact_store.put("c_key", "val")
        keys = await fact_store.list_keys()
        assert keys == ["a_key", "b_key", "c_key"]

    async def test_list_keys_with_prefix(self, fact_store: InMemoryFactStore) -> None:
        await fact_store.put("user_name", "Alice")
        await fact_store.put("user_age", "30")
        await fact_store.put("account_id", "123")
        keys = await fact_store.list_keys(prefix="user_")
        assert keys == ["user_age", "user_name"]

    async def test_get_all(self, fact_store: InMemoryFactStore) -> None:
        await fact_store.put("k1", "v1")
        await fact_store.put("k2", "v2")
        all_facts = await fact_store.get_all()
        assert all_facts == {"k1": "v1", "k2": "v2"}

    async def test_delete(self, fact_store: InMemoryFactStore) -> None:
        await fact_store.put("key", "value")
        await fact_store.delete("key")
        result = await fact_store.get("key")
        assert result is None

    async def test_delete_missing_raises(self, fact_store: InMemoryFactStore) -> None:
        with pytest.raises(KeyError):
            await fact_store.delete("missing")

    async def test_roundtrip(self, fact_store: InMemoryFactStore) -> None:
        await fact_store.put("name", "Alice", metadata={"source": "test"})
        data = fact_store.to_dict()
        restored = InMemoryFactStore.from_dict(data)
        result = await restored.get("name")
        assert result is not None
        assert result[0] == "Alice"
        assert result[1]["source"] == "test"

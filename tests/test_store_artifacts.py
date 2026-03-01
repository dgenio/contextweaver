"""Tests for contextweaver.store.artifacts -- async put/get/delete/TTL/drilldown selectors."""

from __future__ import annotations

import json
import time

import pytest

from contextweaver.exceptions import ArtifactNotFoundError
from contextweaver.store.artifacts import InMemoryArtifactStore


class TestInMemoryArtifactStore:
    """Tests for InMemoryArtifactStore async methods."""

    async def test_put_and_get(self, artifact_store: InMemoryArtifactStore) -> None:
        await artifact_store.put("h1", "hello world", metadata={"key": "val"})
        payload, meta = await artifact_store.get("h1")
        assert payload == "hello world"
        assert meta["key"] == "val"

    async def test_put_bytes(self, artifact_store: InMemoryArtifactStore) -> None:
        await artifact_store.put("h2", b"binary data")
        payload, _ = await artifact_store.get("h2")
        assert payload == b"binary data"

    async def test_get_missing_raises(self, artifact_store: InMemoryArtifactStore) -> None:
        with pytest.raises(ArtifactNotFoundError):
            await artifact_store.get("missing")

    async def test_delete(self, artifact_store: InMemoryArtifactStore) -> None:
        await artifact_store.put("h3", "data")
        await artifact_store.delete("h3")
        with pytest.raises(ArtifactNotFoundError):
            await artifact_store.get("h3")

    async def test_delete_missing_raises(self, artifact_store: InMemoryArtifactStore) -> None:
        with pytest.raises(ArtifactNotFoundError):
            await artifact_store.delete("missing")

    async def test_exists(self, artifact_store: InMemoryArtifactStore) -> None:
        await artifact_store.put("h4", "data")
        assert await artifact_store.exists("h4") is True
        assert await artifact_store.exists("missing") is False

    async def test_metadata(self, artifact_store: InMemoryArtifactStore) -> None:
        await artifact_store.put("h5", "data", metadata={"tool": "search"})
        meta = await artifact_store.metadata("h5")
        assert meta["tool"] == "search"

    async def test_ttl_eviction(self) -> None:
        store = InMemoryArtifactStore()
        await store.put("h_ttl", "short-lived", ttl_seconds=0)
        # TTL of 0 means it expires immediately (at time.time() + 0)
        time.sleep(0.01)
        with pytest.raises(ArtifactNotFoundError):
            await store.get("h_ttl")

    async def test_drilldown_head(self, artifact_store: InMemoryArtifactStore) -> None:
        text = "A" * 1000
        await artifact_store.put("h_head", text)
        result = await artifact_store.drilldown("h_head", {"type": "head", "chars": 100})
        assert len(result) == 100

    async def test_drilldown_lines(self, artifact_store: InMemoryArtifactStore) -> None:
        text = "line0\nline1\nline2\nline3\nline4"
        await artifact_store.put("h_lines", text)
        result = await artifact_store.drilldown("h_lines", {"type": "lines", "start": 1, "end": 3})
        assert result == "line1\nline2"

    async def test_drilldown_json_keys(self, artifact_store: InMemoryArtifactStore) -> None:
        data = json.dumps({"name": "Alice", "age": 30, "city": "NYC"})
        await artifact_store.put("h_json", data)
        result = await artifact_store.drilldown(
            "h_json", {"type": "json_keys", "keys": ["name", "city"]}
        )
        parsed = json.loads(result)
        assert parsed["name"] == "Alice"
        assert parsed["city"] == "NYC"
        assert "age" not in parsed

    async def test_drilldown_rows(self, artifact_store: InMemoryArtifactStore) -> None:
        data = json.dumps([{"id": i, "val": f"v{i}"} for i in range(10)])
        await artifact_store.put("h_rows", data)
        result = await artifact_store.drilldown("h_rows", {"type": "rows", "start": 0, "end": 2})
        parsed = json.loads(result)
        assert len(parsed) == 2
        assert parsed[0]["id"] == 0

    async def test_list_refs_sorted(self, artifact_store: InMemoryArtifactStore) -> None:
        await artifact_store.put("z1", "z")
        await artifact_store.put("a1", "a")
        refs = artifact_store.list_refs()
        assert refs == ["a1", "z1"]

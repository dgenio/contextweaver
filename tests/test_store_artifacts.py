"""Tests for contextweaver.store.artifacts."""

from __future__ import annotations

import pytest

from contextweaver.exceptions import ArtifactNotFoundError
from contextweaver.store.artifacts import InMemoryArtifactStore


def test_put_and_get() -> None:
    store = InMemoryArtifactStore()
    ref = store.put("h1", b"hello world", media_type="text/plain", label="test")
    assert ref.handle == "h1"
    assert ref.size_bytes == 11
    assert store.get("h1") == b"hello world"


def test_ref_returns_metadata() -> None:
    store = InMemoryArtifactStore()
    store.put("h2", b"data", media_type="application/json")
    r = store.ref("h2")
    assert r.media_type == "application/json"


def test_get_missing_raises() -> None:
    store = InMemoryArtifactStore()
    with pytest.raises(ArtifactNotFoundError):
        store.get("missing")


def test_ref_missing_raises() -> None:
    store = InMemoryArtifactStore()
    with pytest.raises(ArtifactNotFoundError):
        store.ref("missing")


def test_delete() -> None:
    store = InMemoryArtifactStore()
    store.put("h3", b"x")
    store.delete("h3")
    with pytest.raises(ArtifactNotFoundError):
        store.get("h3")


def test_delete_missing_raises() -> None:
    store = InMemoryArtifactStore()
    with pytest.raises(ArtifactNotFoundError):
        store.delete("missing")


def test_list_refs_sorted() -> None:
    store = InMemoryArtifactStore()
    store.put("z1", b"z")
    store.put("a1", b"a")
    refs = store.list_refs()
    assert [r.handle for r in refs] == ["a1", "z1"]


def test_to_dict() -> None:
    store = InMemoryArtifactStore()
    store.put("h1", b"data")
    d = store.to_dict()
    assert len(d["refs"]) == 1


# -- new methods: exists, metadata, drilldown --------------------------------


def test_exists_true() -> None:
    store = InMemoryArtifactStore()
    store.put("h1", b"data")
    assert store.exists("h1") is True


def test_exists_false() -> None:
    store = InMemoryArtifactStore()
    assert store.exists("missing") is False


def test_metadata() -> None:
    store = InMemoryArtifactStore()
    store.put("h1", b"hello", media_type="text/plain", label="test")
    meta = store.metadata("h1")
    assert meta["handle"] == "h1"
    assert meta["media_type"] == "text/plain"
    assert meta["size_bytes"] == 5
    assert meta["label"] == "test"


def test_metadata_missing_raises() -> None:
    store = InMemoryArtifactStore()
    with pytest.raises(ArtifactNotFoundError):
        store.metadata("missing")


def test_drilldown_lines() -> None:
    store = InMemoryArtifactStore()
    text = "line0\nline1\nline2\nline3\nline4"
    store.put("h1", text.encode())
    result = store.drilldown("h1", {"type": "lines", "start": 1, "end": 3})
    assert result == "line1\nline2"


def test_drilldown_head() -> None:
    store = InMemoryArtifactStore()
    store.put("h1", b"abcdefghij" * 100)
    result = store.drilldown("h1", {"type": "head", "chars": 10})
    assert result == "abcdefghij"


def test_drilldown_json_keys() -> None:
    import json

    store = InMemoryArtifactStore()
    obj = {"name": "Alice", "age": 30, "city": "NYC"}
    store.put("h1", json.dumps(obj).encode())
    result = store.drilldown("h1", {"type": "json_keys", "keys": ["name", "age"]})
    parsed = json.loads(result)
    assert parsed == {"age": 30, "name": "Alice"}


def test_drilldown_rows() -> None:
    store = InMemoryArtifactStore()
    text = "header\nrow1\nrow2\nrow3\nrow4"
    store.put("h1", text.encode())
    result = store.drilldown("h1", {"type": "rows", "start": 0, "end": 2})
    assert result == "header\nrow1"


def test_drilldown_missing_raises() -> None:
    store = InMemoryArtifactStore()
    with pytest.raises(ArtifactNotFoundError):
        store.drilldown("missing", {"type": "head", "chars": 10})

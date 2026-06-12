"""Tests for contextweaver.store.artifacts."""

from __future__ import annotations

import json

import pytest

from contextweaver.exceptions import ArtifactNotFoundError, ContextWeaverError
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


def test_from_dict_restores_metadata() -> None:
    store = InMemoryArtifactStore()
    store.put("h1", b"hello", media_type="text/plain", label="greeting")
    store.put("h2", b"world", media_type="application/json")
    restored = InMemoryArtifactStore.from_dict(store.to_dict())
    refs = {r.handle: r for r in restored.list_refs()}
    assert "h1" in refs
    assert refs["h1"].media_type == "text/plain"
    assert refs["h1"].label == "greeting"
    assert refs["h1"].size_bytes == 5
    assert "h2" in refs


def test_from_dict_empty() -> None:
    restored = InMemoryArtifactStore.from_dict({"refs": []})
    assert restored.list_refs() == []


def test_round_trip_restores_raw_bytes() -> None:
    """to_dict/from_dict is lossless: raw bytes survive the round-trip (#466)."""
    store = InMemoryArtifactStore()
    store.put("h1", b"data", media_type="text/plain")
    restored = InMemoryArtifactStore.from_dict(store.to_dict())
    assert restored.exists("h1")
    assert restored.get("h1") == b"data"
    assert restored.drilldown("h1", {"type": "head", "chars": 2}) == "da"


def test_from_dict_metadata_only_get_raises() -> None:
    """A ref without a matching ``data`` entry restores metadata-only (#466)."""
    store = InMemoryArtifactStore()
    store.put("h1", b"data")
    payload = store.to_dict()
    payload["data"] = {}  # simulate a legacy / metadata-only serialisation
    restored = InMemoryArtifactStore.from_dict(payload)
    assert restored.ref("h1").handle == "h1"
    with pytest.raises(ArtifactNotFoundError):
        restored.get("h1")


def test_put_populates_content_hash() -> None:
    """put() stamps a sha256 content_hash on the returned ref (#466)."""
    import hashlib

    store = InMemoryArtifactStore()
    ref = store.put("h1", b"hello world")
    assert ref.content_hash == hashlib.sha256(b"hello world").hexdigest()
    assert store.ref("h1").content_hash == ref.content_hash


def test_exists_true() -> None:
    store = InMemoryArtifactStore()
    store.put("h1", b"data")
    assert store.exists("h1") is True


def test_exists_false() -> None:
    store = InMemoryArtifactStore()
    assert store.exists("missing") is False


def test_metadata_alias() -> None:
    store = InMemoryArtifactStore()
    store.put("h1", b"data", media_type="text/plain", label="lbl")
    m = store.metadata("h1")
    assert m.handle == "h1"
    assert m.media_type == "text/plain"
    assert m.label == "lbl"


def test_metadata_missing_raises() -> None:
    store = InMemoryArtifactStore()
    with pytest.raises(ArtifactNotFoundError):
        store.metadata("missing")


def test_drilldown_head() -> None:
    store = InMemoryArtifactStore()
    store.put("h1", b"hello world from drilldown")
    result = store.drilldown("h1", {"type": "head", "chars": 5})
    assert result == "hello"


def test_drilldown_lines() -> None:
    store = InMemoryArtifactStore()
    content = "line0\nline1\nline2\nline3\nline4"
    store.put("h1", content.encode())
    result = store.drilldown("h1", {"type": "lines", "start": 1, "end": 3})
    assert result == "line1\nline2"


def test_drilldown_json_keys() -> None:
    store = InMemoryArtifactStore()
    data = json.dumps({"name": "Alice", "age": 30, "role": "admin"})
    store.put("h1", data.encode())
    result = store.drilldown("h1", {"type": "json_keys", "keys": ["name", "role"]})
    parsed = json.loads(result)
    assert parsed["name"] == "Alice"
    assert parsed["role"] == "admin"
    assert "age" not in parsed


def test_drilldown_json_keys_invalid_json() -> None:
    store = InMemoryArtifactStore()
    store.put("h1", b"not json")
    result = store.drilldown("h1", {"type": "json_keys", "keys": ["a"]})
    assert result == ""


def test_drilldown_rows() -> None:
    store = InMemoryArtifactStore()
    content = "header\nrow1\nrow2\nrow3"
    store.put("h1", content.encode())
    result = store.drilldown("h1", {"type": "rows", "start": 0, "end": 2})
    assert result == "header\nrow1"


def test_drilldown_unknown_type_raises() -> None:
    store = InMemoryArtifactStore()
    store.put("h1", b"data")
    with pytest.raises(ContextWeaverError, match="Unknown drilldown"):
        store.drilldown("h1", {"type": "unknown"})


def test_drilldown_missing_raises() -> None:
    store = InMemoryArtifactStore()
    with pytest.raises(ArtifactNotFoundError):
        store.drilldown("missing", {"type": "head"})

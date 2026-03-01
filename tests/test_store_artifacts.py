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

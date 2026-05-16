"""Tests for contextweaver.store.json_file_artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contextweaver.exceptions import ArtifactNotFoundError, ContextWeaverError
from contextweaver.store.json_file_artifacts import JsonFileArtifactStore

# ---------------------------------------------------------------------------
# put / get / ref
# ---------------------------------------------------------------------------


def test_put_and_get(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path)
    ref = store.put("h1", b"hello world", media_type="text/plain", label="test")
    assert ref.handle == "h1"
    assert ref.size_bytes == 11
    assert store.get("h1") == b"hello world"


def test_put_creates_data_and_metadata_files(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path)
    store.put("h1", b"hello", media_type="text/plain", label="greeting")
    assert (tmp_path / "h1.data").is_file()
    assert (tmp_path / "h1.json").is_file()
    meta = json.loads((tmp_path / "h1.json").read_text(encoding="utf-8"))
    assert meta["handle"] == "h1"
    assert meta["media_type"] == "text/plain"
    assert meta["label"] == "greeting"


def test_ref_returns_metadata(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path)
    store.put("h2", b"data", media_type="application/json")
    r = store.ref("h2")
    assert r.media_type == "application/json"
    assert r.handle == "h2"


def test_get_missing_raises(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path)
    with pytest.raises(ArtifactNotFoundError, match="missing"):
        store.get("missing")


def test_ref_missing_raises(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path)
    with pytest.raises(ArtifactNotFoundError, match="missing"):
        store.ref("missing")


# ---------------------------------------------------------------------------
# delete / exists / metadata / list_refs
# ---------------------------------------------------------------------------


def test_delete(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path)
    store.put("h3", b"x")
    store.delete("h3")
    assert not (tmp_path / "h3.data").exists()
    assert not (tmp_path / "h3.json").exists()
    with pytest.raises(ArtifactNotFoundError):
        store.get("h3")


def test_delete_missing_raises(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path)
    with pytest.raises(ArtifactNotFoundError):
        store.delete("missing")


def test_exists_true_and_false(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path)
    assert store.exists("missing") is False
    store.put("h1", b"data")
    assert store.exists("h1") is True


def test_metadata_alias(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path)
    store.put("h1", b"data", media_type="text/plain", label="lbl")
    m = store.metadata("h1")
    assert m.handle == "h1"
    assert m.media_type == "text/plain"
    assert m.label == "lbl"


def test_metadata_missing_raises(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path)
    with pytest.raises(ArtifactNotFoundError):
        store.metadata("missing")


def test_list_refs_sorted(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path)
    store.put("z1", b"z")
    store.put("a1", b"a")
    store.put("m1", b"m")
    refs = store.list_refs()
    assert [r.handle for r in refs] == ["a1", "m1", "z1"]


def test_list_refs_empty(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path)
    assert store.list_refs() == []


def test_list_refs_skips_malformed_metadata(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path)
    store.put("ok", b"good")
    # Drop a bogus metadata file with the right extension.
    (tmp_path / "broken.json").write_text("not-json", encoding="utf-8")
    refs = store.list_refs()
    assert [r.handle for r in refs] == ["ok"]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_reinstantiating_recovers_refs(tmp_path: Path) -> None:
    store1 = JsonFileArtifactStore(tmp_path)
    store1.put("a", b"alpha", media_type="text/plain", label="A")
    store1.put("b", b"beta", media_type="application/json", label="B")

    store2 = JsonFileArtifactStore(tmp_path)
    refs = {r.handle: r for r in store2.list_refs()}
    assert set(refs) == {"a", "b"}
    assert refs["a"].label == "A"
    assert refs["b"].media_type == "application/json"
    assert store2.get("a") == b"alpha"


def test_directory_created_if_missing(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested" / "artifacts"
    assert not target.exists()
    JsonFileArtifactStore(target)
    assert target.is_dir()


def test_base_dir_property(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path)
    assert store.base_dir == tmp_path


# ---------------------------------------------------------------------------
# Handle validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["foo/bar", "..", ".", "a\\b", "x\x00y", ""],
)
def test_put_rejects_unsafe_handles(tmp_path: Path, bad: str) -> None:
    store = JsonFileArtifactStore(tmp_path)
    with pytest.raises(ContextWeaverError):
        store.put(bad, b"data")


@pytest.mark.parametrize(
    "bad",
    ["foo/bar", "..", ".", "a\\b", "x\x00y", ""],
)
@pytest.mark.parametrize("method", ["get", "ref", "exists", "delete", "metadata"])
def test_read_path_methods_reject_unsafe_handles(tmp_path: Path, bad: str, method: str) -> None:
    """Path-traversal handles must be rejected by every public read/mutate method.

    Regression for PR #232 audit: previously only ``put()`` validated handles,
    so ``store.get("../secret")`` could resolve outside ``base_dir``.
    """
    store = JsonFileArtifactStore(tmp_path)
    fn = getattr(store, method)
    with pytest.raises(ContextWeaverError):
        fn(bad)


def test_drilldown_rejects_unsafe_handle(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path)
    with pytest.raises(ContextWeaverError):
        store.drilldown("../secret", {"type": "head", "chars": 5})


def test_list_refs_skips_non_mapping_json(tmp_path: Path) -> None:
    """Regression: ``list_refs`` must catch ``TypeError`` from ``ArtifactRef.from_dict``.

    A ``.json`` file whose contents are valid JSON of the wrong shape
    (e.g. ``[]``, ``null``, a bare string) used to crash ``list_refs``
    with ``TypeError`` rather than skipping the entry (PR #232 review).
    """
    store = JsonFileArtifactStore(tmp_path)
    store.put("good", b"data")
    # Plant three malformed metadata files whose JSON parses but whose shape
    # is not a mapping. ArtifactRef.from_dict raises TypeError for each.
    (tmp_path / "list_shape.json").write_text("[]", encoding="utf-8")
    (tmp_path / "null_shape.json").write_text("null", encoding="utf-8")
    (tmp_path / "str_shape.json").write_text('"not a mapping"', encoding="utf-8")

    refs = store.list_refs()
    # Only the well-formed "good" ref survives; the three bad shapes are
    # silently skipped (debug-logged) rather than raising.
    assert [r.handle for r in refs] == ["good"]


# ---------------------------------------------------------------------------
# Drilldown (matches InMemoryArtifactStore byte-for-byte)
# ---------------------------------------------------------------------------


def test_drilldown_head(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path)
    store.put("h1", b"hello world from drilldown")
    assert store.drilldown("h1", {"type": "head", "chars": 5}) == "hello"


def test_drilldown_lines(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path)
    store.put("h1", b"line0\nline1\nline2\nline3\nline4")
    assert store.drilldown("h1", {"type": "lines", "start": 1, "end": 3}) == "line1\nline2"


def test_drilldown_json_keys(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path)
    store.put("h1", json.dumps({"name": "Alice", "age": 30, "role": "admin"}).encode())
    parsed = json.loads(store.drilldown("h1", {"type": "json_keys", "keys": ["name", "role"]}))
    assert parsed == {"name": "Alice", "role": "admin"}


def test_drilldown_json_keys_invalid_json(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path)
    store.put("h1", b"not json")
    assert store.drilldown("h1", {"type": "json_keys", "keys": ["a"]}) == ""


def test_drilldown_rows(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path)
    store.put("h1", b"header\nrow1\nrow2\nrow3")
    assert store.drilldown("h1", {"type": "rows", "start": 0, "end": 2}) == "header\nrow1"


def test_drilldown_unknown_type_raises(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path)
    store.put("h1", b"data")
    with pytest.raises(ContextWeaverError, match="Unknown drilldown"):
        store.drilldown("h1", {"type": "unknown"})


def test_drilldown_missing_raises(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path)
    with pytest.raises(ArtifactNotFoundError):
        store.drilldown("missing", {"type": "head"})

"""Tests for contextweaver.store.json_file_artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from contextweaver.exceptions import (
    ArtifactNotFoundError,
    ArtifactStoreQuotaError,
    ContextWeaverError,
)
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


# ---------------------------------------------------------------------------
# content_hash persistence (#466)
# ---------------------------------------------------------------------------


def test_put_persists_content_hash(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path)
    ref = store.put("h1", b"hello world")
    expected = hashlib.sha256(b"hello world").hexdigest()
    assert ref.content_hash == expected
    # Persisted to disk and recovered by a fresh instance — this is what lets
    # the firewall's #190 idempotency short-circuit survive a restart.
    reopened = JsonFileArtifactStore(tmp_path)
    assert reopened.ref("h1").content_hash == expected


# ---------------------------------------------------------------------------
# Filename encoding for hostile-but-legal handles (#466)
# ---------------------------------------------------------------------------


def test_colon_handle_round_trips_and_is_encoded(tmp_path: Path) -> None:
    handle = "artifact:result:call_1"  # the shape the firewall emits
    store = JsonFileArtifactStore(tmp_path)
    store.put(handle, b"payload", media_type="text/plain")
    assert store.get(handle) == b"payload"
    assert store.ref(handle).handle == handle
    # No raw ':' in any on-disk filename (NTFS alternate-data-stream safety).
    names = [p.name for p in tmp_path.iterdir()]
    assert names, "expected files on disk"
    assert all(":" not in name for name in names)
    # Recovered after re-instantiation by its original handle.
    assert JsonFileArtifactStore(tmp_path).get(handle) == b"payload"


def test_colon_handle_in_list_refs(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path)
    store.put("artifact:a", b"a")
    store.put("artifact:b", b"b")
    assert [r.handle for r in store.list_refs()] == ["artifact:a", "artifact:b"]


# ---------------------------------------------------------------------------
# Atomic writes (#497)
# ---------------------------------------------------------------------------


def test_put_leaves_no_temp_files(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path)
    store.put("h1", b"data")
    assert [p.name for p in tmp_path.glob("._cw_tmp_*")] == []


def test_overwrite_replaces_atomically(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path)
    store.put("h1", b"v1")
    store.put("h1", b"v2-longer")
    assert store.get("h1") == b"v2-longer"
    assert store.ref("h1").size_bytes == len(b"v2-longer")
    assert [r.handle for r in store.list_refs()] == ["h1"]


# ---------------------------------------------------------------------------
# In-memory index (#497)
# ---------------------------------------------------------------------------


def test_list_refs_does_not_rescan_directory(tmp_path: Path) -> None:
    """list_refs reads the in-memory index, not the filesystem (#497).

    A metadata file dropped into the directory *after* construction is not
    visible to ``list_refs`` until the store is re-instantiated.
    """
    store = JsonFileArtifactStore(tmp_path)
    store.put("h1", b"a")
    (tmp_path / "sneaky.json").write_text(
        json.dumps({"handle": "sneaky", "media_type": "text/plain", "size_bytes": 1}),
        encoding="utf-8",
    )
    assert [r.handle for r in store.list_refs()] == ["h1"]
    # A fresh instance scans the directory once and picks it up.
    assert {r.handle for r in JsonFileArtifactStore(tmp_path).list_refs()} == {"h1", "sneaky"}


def test_delete_updates_index(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path)
    store.put("h1", b"a")
    store.put("h2", b"b")
    store.delete("h1")
    assert [r.handle for r in store.list_refs()] == ["h2"]
    assert store.exists("h1") is False


# ---------------------------------------------------------------------------
# Size quotas (#497)
# ---------------------------------------------------------------------------


def test_max_artifacts_quota(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path, max_artifacts=2)
    store.put("h1", b"a")
    store.put("h2", b"b")
    with pytest.raises(ArtifactStoreQuotaError, match="count limit"):
        store.put("h3", b"c")
    # Overwriting an existing handle does not count as a new artifact.
    store.put("h1", b"aa")
    assert store.get("h1") == b"aa"


def test_max_bytes_quota(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path, max_bytes=10)
    store.put("h1", b"12345")  # 5 bytes
    with pytest.raises(ArtifactStoreQuotaError, match="byte limit"):
        store.put("h2", b"123456")  # would total 11 > 10
    # Replacing h1 with smaller content frees room.
    store.put("h1", b"1")
    store.put("h2", b"123456")
    assert store.get("h2") == b"123456"


def test_quota_failure_does_not_store_partial(tmp_path: Path) -> None:
    store = JsonFileArtifactStore(tmp_path, max_bytes=4)
    with pytest.raises(ArtifactStoreQuotaError):
        store.put("h1", b"12345")
    assert store.exists("h1") is False
    assert not (tmp_path / "h1.data").exists()

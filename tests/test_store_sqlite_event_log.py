"""Tests for contextweaver.store.sqlite_event_log."""

from __future__ import annotations

from pathlib import Path

import pytest

from contextweaver.exceptions import (
    ContextWeaverError,
    DuplicateItemError,
    ItemNotFoundError,
    StoreClosedError,
)
from contextweaver.store._sqlite_base import schema_version
from contextweaver.store.sqlite_event_log import MIGRATIONS, SqliteEventLog
from contextweaver.types import ArtifactRef, ContextItem, ItemKind, Sensitivity


def _make_item(iid: str, kind: ItemKind = ItemKind.user_turn, text: str = "text") -> ContextItem:
    return ContextItem(id=iid, kind=kind, text=text)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_open_creates_file_and_applies_migrations(tmp_path: Path) -> None:
    db = tmp_path / "session.db"
    log = SqliteEventLog(db)
    assert db.is_file()
    assert schema_version(log._require_conn()) == len(MIGRATIONS) - 1
    log.close()


def test_open_creates_missing_parent_directory(tmp_path: Path) -> None:
    db = tmp_path / "nested" / "dir" / "session.db"
    log = SqliteEventLog(db)
    assert db.is_file()
    log.close()


def test_wal_mode_enabled(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "wal.db")
    row = log._require_conn().execute("PRAGMA journal_mode").fetchone()
    assert row[0] == "wal"
    log.close()


def test_close_is_idempotent(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "x.db")
    log.close()
    log.close()  # no exception


def test_use_after_close_raises(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "x.db")
    log.close()
    # Use-after-close must raise the contextweaver-family exception so callers
    # can catch the ContextWeaverError hierarchy uniformly (per AGENTS.md).
    with pytest.raises(StoreClosedError, match="closed"):
        log.append(_make_item("i1"))
    # And it must remain catchable as ContextWeaverError for cross-family code.
    with pytest.raises(ContextWeaverError):
        log.append(_make_item("i2"))


def test_context_manager(tmp_path: Path) -> None:
    db = tmp_path / "ctx.db"
    with SqliteEventLog(db) as log:
        log.append(_make_item("i1"))
        assert log.count() == 1
    # Reopen — data persists.
    with SqliteEventLog(db) as log2:
        assert log2.get("i1").id == "i1"


# ---------------------------------------------------------------------------
# Append / get
# ---------------------------------------------------------------------------


def test_append_and_get(tmp_path: Path) -> None:
    with SqliteEventLog(tmp_path / "x.db") as log:
        log.append(_make_item("i1"))
        assert log.get("i1").text == "text"


def test_duplicate_raises(tmp_path: Path) -> None:
    with SqliteEventLog(tmp_path / "x.db") as log:
        log.append(_make_item("i1"))
        with pytest.raises(DuplicateItemError, match="Duplicate item id"):
            log.append(_make_item("i1"))


def test_get_missing_raises(tmp_path: Path) -> None:
    with (
        SqliteEventLog(tmp_path / "x.db") as log,
        pytest.raises(ItemNotFoundError, match="Item not found"),
    ):
        log.get("missing")


def test_append_preserves_all_context_item_fields(tmp_path: Path) -> None:
    artifact = ArtifactRef(handle="h1", media_type="text/plain", size_bytes=11, label="lbl")
    item = ContextItem(
        id="i1",
        kind=ItemKind.tool_result,
        text="payload",
        token_estimate=42,
        sensitivity=Sensitivity.confidential,
        metadata={"k": "v", "n": 1},
        parent_id="p1",
        artifact_ref=artifact,
    )
    with SqliteEventLog(tmp_path / "x.db") as log:
        log.append(item)
        round_trip = log.get("i1")
    assert round_trip.token_estimate == 42
    assert round_trip.sensitivity == Sensitivity.confidential
    assert round_trip.metadata == {"k": "v", "n": 1}
    assert round_trip.parent_id == "p1"
    assert round_trip.artifact_ref is not None
    assert round_trip.artifact_ref.handle == "h1"
    assert round_trip.artifact_ref.size_bytes == 11


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def test_all_returns_insertion_order(tmp_path: Path) -> None:
    with SqliteEventLog(tmp_path / "x.db") as log:
        for i in range(5):
            log.append(_make_item(f"i{i}"))
        assert [item.id for item in log.all()] == [f"i{i}" for i in range(5)]


def test_filter_by_kind(tmp_path: Path) -> None:
    with SqliteEventLog(tmp_path / "x.db") as log:
        log.append(_make_item("u1", ItemKind.user_turn))
        log.append(_make_item("t1", ItemKind.tool_call))
        log.append(_make_item("u2", ItemKind.user_turn))
        results = log.filter_by_kind(ItemKind.user_turn)
    assert [r.id for r in results] == ["u1", "u2"]


def test_filter_by_kind_no_kinds(tmp_path: Path) -> None:
    with SqliteEventLog(tmp_path / "x.db") as log:
        log.append(_make_item("u1"))
        assert log.filter_by_kind() == []


def test_tail(tmp_path: Path) -> None:
    with SqliteEventLog(tmp_path / "x.db") as log:
        for i in range(10):
            log.append(_make_item(f"i{i}"))
        tail = log.tail(3)
    assert [item.id for item in tail] == ["i7", "i8", "i9"]


def test_tail_zero(tmp_path: Path) -> None:
    with SqliteEventLog(tmp_path / "x.db") as log:
        log.append(_make_item("i1"))
        assert log.tail(0) == []
        assert log.tail(-1) == []


def test_children(tmp_path: Path) -> None:
    with SqliteEventLog(tmp_path / "x.db") as log:
        log.append(_make_item("parent1"))
        log.append(
            ContextItem(id="child1", kind=ItemKind.tool_result, text="r1", parent_id="parent1")
        )
        log.append(
            ContextItem(id="child2", kind=ItemKind.tool_result, text="r2", parent_id="parent1")
        )
        log.append(_make_item("other"))
        children = log.children("parent1")
    assert [c.id for c in children] == ["child1", "child2"]


def test_children_empty(tmp_path: Path) -> None:
    with SqliteEventLog(tmp_path / "x.db") as log:
        log.append(_make_item("i1"))
        assert log.children("i1") == []


def test_parent_found(tmp_path: Path) -> None:
    with SqliteEventLog(tmp_path / "x.db") as log:
        log.append(_make_item("p1"))
        log.append(ContextItem(id="c1", kind=ItemKind.tool_result, text="r", parent_id="p1"))
        parent = log.parent("c1")
    assert parent is not None
    assert parent.id == "p1"


def test_parent_none(tmp_path: Path) -> None:
    with SqliteEventLog(tmp_path / "x.db") as log:
        log.append(_make_item("i1"))
        assert log.parent("i1") is None


def test_parent_missing_parent_id(tmp_path: Path) -> None:
    with SqliteEventLog(tmp_path / "x.db") as log:
        log.append(ContextItem(id="c1", kind=ItemKind.tool_result, text="r", parent_id="missing"))
        assert log.parent("c1") is None


def test_parent_not_found_raises(tmp_path: Path) -> None:
    with SqliteEventLog(tmp_path / "x.db") as log, pytest.raises(ItemNotFoundError):
        log.parent("missing")


def test_query_no_filters(tmp_path: Path) -> None:
    with SqliteEventLog(tmp_path / "x.db") as log:
        for i in range(5):
            log.append(_make_item(f"i{i}"))
        assert len(log.query()) == 5


def test_query_with_kinds(tmp_path: Path) -> None:
    with SqliteEventLog(tmp_path / "x.db") as log:
        log.append(_make_item("u1", ItemKind.user_turn))
        log.append(_make_item("t1", ItemKind.tool_call))
        log.append(_make_item("u2", ItemKind.user_turn))
        results = log.query(kinds=[ItemKind.user_turn])
    assert [r.id for r in results] == ["u1", "u2"]


def test_query_with_empty_kinds_returns_empty(tmp_path: Path) -> None:
    with SqliteEventLog(tmp_path / "x.db") as log:
        log.append(_make_item("u1"))
        assert log.query(kinds=[]) == []


def test_query_with_since(tmp_path: Path) -> None:
    with SqliteEventLog(tmp_path / "x.db") as log:
        for i in range(5):
            log.append(_make_item(f"i{i}"))
        results = log.query(since=3)
    assert [r.id for r in results] == ["i3", "i4"]


def test_query_with_limit(tmp_path: Path) -> None:
    with SqliteEventLog(tmp_path / "x.db") as log:
        for i in range(5):
            log.append(_make_item(f"i{i}"))
        results = log.query(limit=2)
    assert [r.id for r in results] == ["i0", "i1"]


def test_query_combined_filters(tmp_path: Path) -> None:
    """Combined ``kinds`` + ``since`` + ``limit`` matches the in-memory backend.

    For ``[u1, t1, u2, t2, u3]`` and ``kinds=[user_turn], since=2, limit=1``:
    ``since=2`` slices the full log to ``[u2, t2, u3]``, the kind filter then
    reduces to ``[u2, u3]``, and ``limit=1`` returns ``[u2]``. Earlier the
    SQLite path filtered by kind in SQL *before* applying ``since`` over the
    filtered list, which gave ``[u3]`` — diverging from InMemoryEventLog and
    flagged in PR #232 review.
    """
    with SqliteEventLog(tmp_path / "x.db") as log:
        log.append(_make_item("u1", ItemKind.user_turn))
        log.append(_make_item("t1", ItemKind.tool_call))
        log.append(_make_item("u2", ItemKind.user_turn))
        log.append(_make_item("t2", ItemKind.tool_call))
        log.append(_make_item("u3", ItemKind.user_turn))
        results = log.query(kinds=[ItemKind.user_turn], since=2, limit=1)
    assert [r.id for r in results] == ["u2"]


def test_query_kinds_since_matches_in_memory_semantics(tmp_path: Path) -> None:
    """Regression: ``since`` is applied to the *full* log before ``kinds`` filters.

    Mirror of ``tests/test_store_event_log.py::test_query_combined_filters``
    so the two backends are pinned to byte-identical outputs.
    """
    from contextweaver.store.event_log import InMemoryEventLog

    in_mem = InMemoryEventLog()
    sqlite_log = SqliteEventLog(tmp_path / "parity.db")
    try:
        for iid, kind in [
            ("u1", ItemKind.user_turn),
            ("t1", ItemKind.tool_call),
            ("u2", ItemKind.user_turn),
            ("t2", ItemKind.tool_call),
            ("u3", ItemKind.user_turn),
        ]:
            item = _make_item(iid, kind)
            in_mem.append(item)
            sqlite_log.append(item)

        cases = [
            {"kinds": [ItemKind.user_turn], "since": 2, "limit": 1},
            {"kinds": [ItemKind.user_turn], "since": 2},
            {"kinds": [ItemKind.tool_call], "since": 1, "limit": 1},
            {"since": 3},
        ]
        for kwargs in cases:
            in_ids = [i.id for i in in_mem.query(**kwargs)]  # type: ignore[arg-type]
            sq_ids = [i.id for i in sqlite_log.query(**kwargs)]  # type: ignore[arg-type]
            assert in_ids == sq_ids, f"parity broken for {kwargs}: {in_ids} vs {sq_ids}"
    finally:
        sqlite_log.close()


def test_count_and_len(tmp_path: Path) -> None:
    with SqliteEventLog(tmp_path / "x.db") as log:
        assert log.count() == 0
        assert len(log) == 0
        log.append(_make_item("i1"))
        log.append(_make_item("i2"))
        assert log.count() == 2
        assert len(log) == 2


# ---------------------------------------------------------------------------
# Multi-session persistence (the headline #223 success metric)
# ---------------------------------------------------------------------------


def test_multi_session_round_trip(tmp_path: Path) -> None:
    db = tmp_path / "session.db"
    log1 = SqliteEventLog(db)
    for i in range(7):
        log1.append(_make_item(f"i{i}", text=f"payload-{i}"))
    log1.close()

    log2 = SqliteEventLog(db)
    items = log2.all()
    log2.close()

    assert [item.id for item in items] == [f"i{i}" for i in range(7)]
    assert items[3].text == "payload-3"


def test_reopen_appends_after_existing(tmp_path: Path) -> None:
    db = tmp_path / "session.db"
    with SqliteEventLog(db) as log:
        log.append(_make_item("i0"))
        log.append(_make_item("i1"))
    with SqliteEventLog(db) as log:
        log.append(_make_item("i2"))
        order = [it.id for it in log.all()]
    assert order == ["i0", "i1", "i2"]


def test_reopen_preserves_schema_version(tmp_path: Path) -> None:
    db = tmp_path / "session.db"
    with SqliteEventLog(db) as log:
        first = schema_version(log._require_conn())
    with SqliteEventLog(db) as log:
        second = schema_version(log._require_conn())
    assert first == second == len(MIGRATIONS) - 1


def test_in_memory_path(tmp_path: Path) -> None:
    log = SqliteEventLog(":memory:")
    log.append(_make_item("i1"))
    assert log.path == ":memory:"
    assert log.count() == 1
    log.close()

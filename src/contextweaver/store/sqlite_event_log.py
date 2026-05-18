"""SQLite-backed event log for contextweaver.

The first persistent backend in the SQLite-stores epic (#174).  Implements
the :class:`~contextweaver.store.protocols.EventLog` protocol against a
single-process SQLite file, layered on top of the shared connection +
migration scaffolding in :mod:`contextweaver.store._sqlite_base`.

The store is append-only by API — :meth:`SqliteEventLog.append` is the only
write path, mirroring the :class:`~contextweaver.store.event_log.InMemoryEventLog`
invariant.  Item insertion order is preserved via an auto-incrementing
``ordinal`` column; every read returns items in that order.

Limitations (out of scope for #223; tracked elsewhere):

- **Single process only.**  ``WAL`` mode gives intra-process concurrency,
  not cross-process write coordination.
- **Sync only.**  No ``aiosqlite``-based async variant ships in this slice.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from types import TracebackType
from typing import Any

from contextweaver.exceptions import DuplicateItemError, ItemNotFoundError, StoreClosedError
from contextweaver.store._sqlite_base import Migration, apply_migrations, connect
from contextweaver.types import ContextItem, ItemKind

logger = logging.getLogger("contextweaver.store")

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def _migration_0(conn: sqlite3.Connection) -> None:
    """Initial schema: ``event_log`` table + indexes on ``kind`` / ``parent_id``."""
    conn.execute(
        """
        CREATE TABLE event_log (
            ordinal       INTEGER PRIMARY KEY AUTOINCREMENT,
            id            TEXT    NOT NULL UNIQUE,
            kind          TEXT    NOT NULL,
            text          TEXT    NOT NULL,
            token_estimate INTEGER NOT NULL DEFAULT 0,
            sensitivity   TEXT    NOT NULL,
            metadata      TEXT    NOT NULL,
            parent_id     TEXT,
            artifact_ref  TEXT
        )
        """
    )
    conn.execute("CREATE INDEX idx_event_log_kind ON event_log(kind)")
    conn.execute("CREATE INDEX idx_event_log_parent ON event_log(parent_id)")


MIGRATIONS: list[Migration] = [_migration_0]
"""Ordered schema migrations.  Append (never reorder) when extending."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_item(row: sqlite3.Row) -> ContextItem:
    """Hydrate a :class:`ContextItem` from a ``SELECT *`` row."""
    return ContextItem.from_dict(
        {
            "id": row["id"],
            "kind": row["kind"],
            "text": row["text"],
            "token_estimate": row["token_estimate"],
            "sensitivity": row["sensitivity"],
            "metadata": json.loads(row["metadata"]),
            "parent_id": row["parent_id"],
            "artifact_ref": (
                json.loads(row["artifact_ref"]) if row["artifact_ref"] is not None else None
            ),
        }
    )


def _item_to_row(item: ContextItem) -> tuple[Any, ...]:
    """Flatten a :class:`ContextItem` to the ``event_log`` row tuple."""
    artifact = item.artifact_ref.to_dict() if item.artifact_ref is not None else None
    return (
        item.id,
        item.kind.value,
        item.text,
        item.token_estimate,
        item.sensitivity.value,
        json.dumps(item.metadata, sort_keys=True),
        item.parent_id,
        json.dumps(artifact, sort_keys=True) if artifact is not None else None,
    )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class SqliteEventLog:
    """SQLite-backed implementation of the :class:`EventLog` protocol.

    Pass ``":memory:"`` for a transient in-process database (useful in tests).
    Otherwise, *path* must be a filesystem path to a SQLite file; its parent
    directory is created on first open.

    The constructor opens the connection and applies any pending migrations
    so the store is ready for reads/writes immediately.  Call :meth:`close`
    (or use the store as a context manager) to release the connection.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = ":memory:" if path == ":memory:" else Path(path)
        self._conn: sqlite3.Connection | None = connect(self._path)
        apply_migrations(self._conn, MIGRATIONS)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def path(self) -> str:
        """Return the filesystem path (or ``":memory:"``)."""
        return str(self._path)

    def close(self) -> None:
        """Release the underlying SQLite connection.

        Idempotent — calling :meth:`close` twice is a no-op.  After
        :meth:`close`, every other method raises
        :class:`~contextweaver.exceptions.StoreClosedError`.
        """
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            logger.debug("sqlite_event_log.close: path=%s", self._path)

    def __enter__(self) -> SqliteEventLog:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise StoreClosedError("SqliteEventLog is closed")
        return self._conn

    # ------------------------------------------------------------------
    # EventLog protocol
    # ------------------------------------------------------------------

    def append(self, item: ContextItem) -> None:
        """Append *item* to the log.

        Raises:
            DuplicateItemError: If an item with the same ``id`` already exists.
        """
        conn = self._require_conn()
        try:
            conn.execute(
                "INSERT INTO event_log (id, kind, text, token_estimate, sensitivity, "
                "metadata, parent_id, artifact_ref) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                _item_to_row(item),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateItemError(f"Duplicate item id: {item.id!r}") from exc
        logger.debug("event_log.append: id=%s, kind=%s", item.id, item.kind.value)

    def get(self, item_id: str) -> ContextItem:
        """Return the item with *item_id*.

        Raises:
            ItemNotFoundError: If no item with *item_id* exists.
        """
        conn = self._require_conn()
        row = conn.execute(
            "SELECT * FROM event_log WHERE id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            raise ItemNotFoundError(f"Item not found: {item_id!r}")
        return _row_to_item(row)

    def all(self) -> list[ContextItem]:
        """Return all items in insertion order."""
        conn = self._require_conn()
        rows = conn.execute("SELECT * FROM event_log ORDER BY ordinal ASC").fetchall()
        return [_row_to_item(r) for r in rows]

    def filter_by_kind(self, *kinds: ItemKind) -> list[ContextItem]:
        """Return all items whose ``kind`` is in *kinds*."""
        if not kinds:
            return []
        conn = self._require_conn()
        placeholders = ",".join("?" for _ in kinds)
        rows = conn.execute(
            f"SELECT * FROM event_log WHERE kind IN ({placeholders}) ORDER BY ordinal ASC",
            tuple(k.value for k in kinds),
        ).fetchall()
        return [_row_to_item(r) for r in rows]

    def tail(self, n: int) -> list[ContextItem]:
        """Return the last *n* items.

        ``n <= 0`` returns an empty list, matching
        :class:`~contextweaver.store.event_log.InMemoryEventLog`.
        """
        if n <= 0:
            return []
        conn = self._require_conn()
        rows = conn.execute(
            "SELECT * FROM event_log ORDER BY ordinal DESC LIMIT ?",
            (n,),
        ).fetchall()
        return [_row_to_item(r) for r in reversed(rows)]

    def children(self, parent_id: str) -> list[ContextItem]:
        """Return all items whose ``parent_id`` equals *parent_id*."""
        conn = self._require_conn()
        rows = conn.execute(
            "SELECT * FROM event_log WHERE parent_id = ? ORDER BY ordinal ASC",
            (parent_id,),
        ).fetchall()
        return [_row_to_item(r) for r in rows]

    def parent(self, item_id: str) -> ContextItem | None:
        """Return the parent of *item_id*, or ``None``.

        Raises:
            ItemNotFoundError: If no item with *item_id* exists.
        """
        item = self.get(item_id)
        if item.parent_id is None:
            return None
        try:
            return self.get(item.parent_id)
        except ItemNotFoundError:
            return None

    def query(
        self,
        kinds: list[ItemKind] | None = None,
        since: int | None = None,
        limit: int | None = None,
    ) -> list[ContextItem]:
        """Flexible query over the event log.

        Matches the in-memory backend's filter order exactly: ``since`` is
        a positional slice over the **full insertion-ordered log**, applied
        *before* the ``kinds`` filter. Applying ``kinds`` first (the
        natural SQL path) would give different results on mixed-kind logs
        — e.g. ``kinds=[B], since=2`` over ``[A, A, B, A, B]`` must yield
        ``[B]`` (drop first 2 of full list → ``[B, A, B]`` → kind filter
        → ``[B, B]``), not the empty list.

        Args:
            kinds: If given, only include items whose kind is in this list.
            since: If given, only include items at or after this positional
                index (zero-based, insertion order over the full log).
            limit: If given, return at most this many items.
        """
        if kinds is not None and not kinds:
            return []
        conn = self._require_conn()
        rows = conn.execute("SELECT * FROM event_log ORDER BY ordinal ASC").fetchall()
        items = [_row_to_item(r) for r in rows]
        if since is not None:
            items = items[since:]
        if kinds is not None:
            kind_set = set(kinds)
            items = [i for i in items if i.kind in kind_set]
        if limit is not None:
            items = items[:limit]
        return items

    def count(self) -> int:
        """Return the number of items in the log."""
        conn = self._require_conn()
        row = conn.execute("SELECT COUNT(*) AS c FROM event_log").fetchone()
        return int(row["c"])

    def __len__(self) -> int:
        return self.count()

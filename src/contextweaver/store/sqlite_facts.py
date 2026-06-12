"""SQLite-backed fact memory store for contextweaver (issue #496).

A persistent :class:`~contextweaver.store.protocols.FactStore` built on the
shared SQLite scaffolding in :mod:`contextweaver.store._sqlite_base`, mirroring
:class:`~contextweaver.store.sqlite_event_log.SqliteEventLog` and
:class:`~contextweaver.store.sqlite_episodic.SqliteEpisodicStore`.

``put`` has **upsert** semantics keyed on ``fact_id`` (matching
:class:`~contextweaver.store.facts.InMemoryFactStore`): writing an existing
``fact_id`` replaces the prior fact.  ``get_by_key`` / :meth:`all` return facts
sorted by ``fact_id`` for deterministic ordering, so swapping this backend for
the in-memory one does not change context-build output.

Limitations are shared with :class:`SqliteEventLog`: single process, sync only,
thread-affine connection (not a valid
:func:`~contextweaver.store.async_bridge.to_async` target).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from types import TracebackType

from contextweaver.exceptions import ItemNotFoundError, StoreClosedError
from contextweaver.store._sqlite_base import Migration, apply_migrations, connect
from contextweaver.store.facts import Fact
from contextweaver.types import Sensitivity

logger = logging.getLogger("contextweaver.store")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def _migration_0(conn: sqlite3.Connection) -> None:
    """Initial schema: ``facts`` table keyed by ``fact_id`` + index on ``key``."""
    conn.execute(
        """
        CREATE TABLE facts (
            fact_id     TEXT PRIMARY KEY,
            key         TEXT NOT NULL,
            value       TEXT NOT NULL,
            tags        TEXT NOT NULL,
            metadata    TEXT NOT NULL,
            sensitivity TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX idx_facts_key ON facts(key)")


MIGRATIONS: list[Migration] = [_migration_0]
"""Ordered schema migrations.  Append (never reorder) when extending."""

VERSION_TABLE = "_contextweaver_schema_version_facts"
"""Distinct schema-version table so this store can share a DB file with the
event log and episodic store without their migration sequences colliding (#496)."""


def _row_to_fact(row: sqlite3.Row) -> Fact:
    """Hydrate a :class:`Fact` from a ``SELECT *`` row."""
    return Fact(
        fact_id=row["fact_id"],
        key=row["key"],
        value=row["value"],
        tags=list(json.loads(row["tags"])),
        metadata=dict(json.loads(row["metadata"])),
        sensitivity=Sensitivity(row["sensitivity"]),
    )


class SqliteFactStore:
    """SQLite-backed implementation of the :class:`FactStore` protocol.

    Pass ``":memory:"`` for a transient in-process database, otherwise a
    filesystem path whose parent directory is created on first open.  Can share
    a database file with the other SQLite stores — each owns a distinct table.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = ":memory:" if path == ":memory:" else Path(path)
        self._conn: sqlite3.Connection | None = connect(self._path)
        apply_migrations(self._conn, MIGRATIONS, version_table=VERSION_TABLE)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def path(self) -> str:
        """Return the filesystem path (or ``":memory:"``)."""
        return str(self._path)

    def close(self) -> None:
        """Release the underlying SQLite connection.  Idempotent."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            logger.debug("sqlite_facts.close: path=%s", self._path)

    def __enter__(self) -> SqliteFactStore:
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
            raise StoreClosedError("SqliteFactStore is closed")
        return self._conn

    # ------------------------------------------------------------------
    # FactStore protocol
    # ------------------------------------------------------------------

    def put(self, fact: Fact) -> None:
        """Insert or replace the fact identified by ``fact.fact_id`` (upsert)."""
        conn = self._require_conn()
        conn.execute(
            "INSERT OR REPLACE INTO facts (fact_id, key, value, tags, metadata, sensitivity) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                fact.fact_id,
                fact.key,
                fact.value,
                json.dumps(list(fact.tags)),
                json.dumps(dict(fact.metadata), sort_keys=True),
                fact.sensitivity.value,
            ),
        )
        logger.debug("sqlite_facts.put: id=%s, key=%s", fact.fact_id, fact.key)

    def get(self, fact_id: str) -> Fact:
        """Return the fact with *fact_id*.

        Raises:
            ItemNotFoundError: If no fact with *fact_id* exists.
        """
        conn = self._require_conn()
        row = conn.execute("SELECT * FROM facts WHERE fact_id = ?", (fact_id,)).fetchone()
        if row is None:
            raise ItemNotFoundError(f"Fact not found: {fact_id!r}")
        return _row_to_fact(row)

    def get_by_key(self, key: str) -> list[Fact]:
        """Return all facts whose ``key`` matches *key*, sorted by ``fact_id``."""
        conn = self._require_conn()
        rows = conn.execute(
            "SELECT * FROM facts WHERE key = ? ORDER BY fact_id ASC",
            (key,),
        ).fetchall()
        return [_row_to_fact(r) for r in rows]

    def list_keys(self, prefix: str = "") -> list[str]:
        """Return all distinct fact keys, optionally filtered by *prefix*."""
        conn = self._require_conn()
        if prefix:
            rows = conn.execute(
                "SELECT DISTINCT key FROM facts WHERE key LIKE ? ESCAPE '\\' ORDER BY key ASC",
                (_like_prefix(prefix),),
            ).fetchall()
        else:
            rows = conn.execute("SELECT DISTINCT key FROM facts ORDER BY key ASC").fetchall()
        return [r["key"] for r in rows]

    def delete(self, fact_id: str) -> None:
        """Remove the fact identified by *fact_id*.

        Raises:
            ItemNotFoundError: If no fact with *fact_id* exists.
        """
        conn = self._require_conn()
        cursor = conn.execute("DELETE FROM facts WHERE fact_id = ?", (fact_id,))
        if cursor.rowcount == 0:
            raise ItemNotFoundError(f"Fact not found: {fact_id!r}")

    def all(self) -> list[Fact]:
        """Return all facts sorted by ``fact_id``."""
        conn = self._require_conn()
        rows = conn.execute("SELECT * FROM facts ORDER BY fact_id ASC").fetchall()
        return [_row_to_fact(r) for r in rows]


def _like_prefix(prefix: str) -> str:
    """Escape LIKE wildcards in *prefix* and append ``%`` for a prefix match."""
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}%"

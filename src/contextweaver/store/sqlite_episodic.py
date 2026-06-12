"""SQLite-backed episodic memory store for contextweaver (issue #496).

A persistent :class:`~contextweaver.store.protocols.EpisodicStore` built on the
shared SQLite scaffolding in :mod:`contextweaver.store._sqlite_base`, mirroring
:class:`~contextweaver.store.sqlite_event_log.SqliteEventLog`.  It completes the
local-persistence story for long-lived agents (durable episodic memory with no
external service) ahead of the remote backends (#426).

Search parity (deterministic): :meth:`SqliteEpisodicStore.search` loads the
episodes and delegates to a transient
:class:`~contextweaver.store.episodic.InMemoryEpisodicStore`, so ranking is
*byte-for-byte identical* to the in-memory backend on identical data — swapping
backends never changes context-build output.  A full scan is acceptable at the
target scale (single process, local file); point to the remote/vector backends
beyond it.

Limitations (shared with :class:`SqliteEventLog`):

- **Single process only.**  ``WAL`` mode gives intra-process concurrency, not
  cross-process write coordination.
- **Sync only.**  No ``aiosqlite``-based async variant ships here; the connection
  is thread-affine (``check_same_thread=True``), so it is not a valid
  :func:`~contextweaver.store.async_bridge.to_async` target.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from types import TracebackType
from typing import Any

from contextweaver.exceptions import ItemNotFoundError, StoreClosedError
from contextweaver.store._sqlite_base import Migration, apply_migrations, connect
from contextweaver.store.episodic import Episode, InMemoryEpisodicStore
from contextweaver.types import Sensitivity

logger = logging.getLogger("contextweaver.store")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def _migration_0(conn: sqlite3.Connection) -> None:
    """Initial schema: ``episodes`` table + index on ``episode_id``."""
    conn.execute(
        """
        CREATE TABLE episodes (
            ordinal     INTEGER PRIMARY KEY AUTOINCREMENT,
            episode_id  TEXT NOT NULL,
            summary     TEXT NOT NULL,
            tags        TEXT NOT NULL,
            metadata    TEXT NOT NULL,
            sensitivity TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX idx_episodes_episode_id ON episodes(episode_id)")


MIGRATIONS: list[Migration] = [_migration_0]
"""Ordered schema migrations.  Append (never reorder) when extending."""

VERSION_TABLE = "_contextweaver_schema_version_episodic"
"""Distinct schema-version table so this store can share a DB file with the
event log and fact store without their migration sequences colliding (#496)."""


def _row_to_episode(row: sqlite3.Row) -> Episode:
    """Hydrate an :class:`Episode` from a ``SELECT *`` row."""
    return Episode(
        episode_id=row["episode_id"],
        summary=row["summary"],
        tags=list(json.loads(row["tags"])),
        metadata=dict(json.loads(row["metadata"])),
        sensitivity=Sensitivity(row["sensitivity"]),
    )


class SqliteEpisodicStore:
    """SQLite-backed implementation of the :class:`EpisodicStore` protocol.

    Pass ``":memory:"`` for a transient in-process database (useful in tests),
    otherwise a filesystem path whose parent directory is created on first open.
    Episodes are append-only and ordered by an auto-incrementing ``ordinal``,
    matching :class:`~contextweaver.store.episodic.InMemoryEpisodicStore`
    (duplicate ``episode_id`` values are allowed; :meth:`get` returns the
    earliest-inserted match).

    The store can share a single database file with
    :class:`~contextweaver.store.sqlite_event_log.SqliteEventLog` and
    :class:`SqliteFactStore` — each owns a distinct table.
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
            logger.debug("sqlite_episodic.close: path=%s", self._path)

    def __enter__(self) -> SqliteEpisodicStore:
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
            raise StoreClosedError("SqliteEpisodicStore is closed")
        return self._conn

    # ------------------------------------------------------------------
    # EpisodicStore protocol
    # ------------------------------------------------------------------

    def add(self, episode: Episode) -> None:
        """Append *episode* to the store."""
        conn = self._require_conn()
        conn.execute(
            "INSERT INTO episodes (episode_id, summary, tags, metadata, sensitivity) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                episode.episode_id,
                episode.summary,
                json.dumps(list(episode.tags)),
                json.dumps(dict(episode.metadata), sort_keys=True),
                episode.sensitivity.value,
            ),
        )
        logger.debug("sqlite_episodic.add: id=%s", episode.episode_id)

    def get(self, episode_id: str) -> Episode | None:
        """Return the earliest-inserted episode with *episode_id*, or ``None``."""
        conn = self._require_conn()
        row = conn.execute(
            "SELECT * FROM episodes WHERE episode_id = ? ORDER BY ordinal ASC LIMIT 1",
            (episode_id,),
        ).fetchone()
        return _row_to_episode(row) if row is not None else None

    def search(self, query: str, top_k: int = 5) -> list[Episode]:
        """Return the *top_k* most relevant episodes for *query*.

        Delegates ranking to a transient
        :class:`~contextweaver.store.episodic.InMemoryEpisodicStore` over
        :meth:`all`, so results are identical to the in-memory backend.
        """
        episodes = self.all()
        if not episodes:
            return []
        scratch = InMemoryEpisodicStore()
        for episode in episodes:
            scratch.add(episode)
        return scratch.search(query, top_k=top_k)

    def all(self) -> list[Episode]:
        """Return all episodes in insertion order."""
        conn = self._require_conn()
        rows = conn.execute("SELECT * FROM episodes ORDER BY ordinal ASC").fetchall()
        return [_row_to_episode(r) for r in rows]

    def latest(self, n: int = 3) -> list[tuple[str, str, dict[str, Any]]]:
        """Return the *n* most recently added episodes, most-recent first."""
        if n <= 0:
            return []
        conn = self._require_conn()
        rows = conn.execute(
            "SELECT * FROM episodes ORDER BY ordinal DESC LIMIT ?",
            (n,),
        ).fetchall()
        return [(r["episode_id"], r["summary"], dict(json.loads(r["metadata"]))) for r in rows]

    def delete(self, episode_id: str) -> None:
        """Remove the earliest-inserted episode with *episode_id*.

        Raises:
            ItemNotFoundError: If no episode with *episode_id* exists.
        """
        conn = self._require_conn()
        row = conn.execute(
            "SELECT ordinal FROM episodes WHERE episode_id = ? ORDER BY ordinal ASC LIMIT 1",
            (episode_id,),
        ).fetchone()
        if row is None:
            raise ItemNotFoundError(f"Episode not found: {episode_id!r}")
        conn.execute("DELETE FROM episodes WHERE ordinal = ?", (row["ordinal"],))

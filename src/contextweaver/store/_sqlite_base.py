"""Shared SQLite connection management for :mod:`contextweaver.store`.

Owned scaffolding the SQLite-backed stores reuse: connection creation with
``WAL`` journal mode and ``foreign_keys=ON`` pragmas, plus a tiny migration
helper driven by a ``_contextweaver_schema_version`` table.

Single-process constraint.  ``WAL`` mode gives reasonable intra-process
read/write concurrency, but cross-process write coordination is out of scope
— deploying SQLite-backed stores from multiple worker processes is
unsupported and a future PostgresSaver-equivalent will fill that gap.

Migrations are plain ``Callable[[sqlite3.Connection], None]`` functions, one
per schema version.  ``apply_migrations`` records the highest applied
version and replays only the missing tail on each open.  This module
intentionally carries no business logic — concrete stores own their schema.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger("contextweaver.store")

Migration = Callable[[sqlite3.Connection], None]
"""A schema migration: applied once, in declared order, inside a transaction."""

_VERSION_TABLE = "_contextweaver_schema_version"


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection configured for the contextweaver stores.

    Sets ``journal_mode=WAL`` (intra-process read/write concurrency) and
    ``foreign_keys=ON``.  The parent directory is created if missing.

    Args:
        path: Filesystem path to the SQLite database file.  ``":memory:"``
            is accepted for transient stores in tests.

    Returns:
        A :class:`sqlite3.Connection` with pragmas applied.

    Raises:
        sqlite3.DatabaseError: If the file exists but is not a SQLite database.
    """
    resolved = Path(path) if path != ":memory:" else None
    if resolved is not None:
        resolved.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(resolved) if resolved is not None else ":memory:",
        isolation_level=None,  # autocommit; we manage transactions explicitly
        check_same_thread=True,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    logger.debug("sqlite.connect: path=%s", resolved or ":memory:")
    return conn


def apply_migrations(
    conn: sqlite3.Connection,
    migrations: list[Migration],
    version_table: str = _VERSION_TABLE,
) -> int:
    """Apply any *migrations* whose index is greater than the recorded version.

    Migration ``i`` (zero-indexed in *migrations*) is replayed if the version
    table reports a strictly lower version.  Each migration runs in its own
    ``BEGIN``/``COMMIT`` block; a failure rolls back and leaves the recorded
    version unchanged.

    Args:
        conn: Connection from :func:`connect`.
        migrations: Ordered list of migration functions.  The list index is
            the schema version each migration leaves the database at.
        version_table: Name of the per-store schema-version table.  Each store
            type that may share a database file (event log, episodic, facts)
            tracks its own migrations under a distinct table, so their
            independent version sequences do not collide.  Internal constant,
            never caller/untrusted input.

    Returns:
        The schema version after the call (``len(migrations) - 1`` on success,
        or the previously-recorded version when no work was needed).

    Raises:
        sqlite3.DatabaseError: If a migration's SQL fails.
    """
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {version_table} ("
        "version INTEGER PRIMARY KEY,"
        "applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    row = conn.execute(f"SELECT MAX(version) AS v FROM {version_table}").fetchone()
    current = -1 if row is None or row["v"] is None else int(row["v"])
    for version, migration in enumerate(migrations):
        if version <= current:
            continue
        logger.debug("sqlite.migrate: table=%s applying version=%d", version_table, version)
        try:
            conn.execute("BEGIN")
            migration(conn)
            conn.execute(
                f"INSERT INTO {version_table} (version) VALUES (?)",
                (version,),
            )
            conn.execute("COMMIT")
        except sqlite3.DatabaseError:
            with contextlib.suppress(sqlite3.DatabaseError):
                conn.execute("ROLLBACK")
            raise
        current = version
    return current


def schema_version(conn: sqlite3.Connection, version_table: str = _VERSION_TABLE) -> int:
    """Return the highest applied migration version for *version_table*, or ``-1``."""
    try:
        row = conn.execute(f"SELECT MAX(version) AS v FROM {version_table}").fetchone()
    except sqlite3.OperationalError:
        return -1
    if row is None or row["v"] is None:
        return -1
    return int(row["v"])

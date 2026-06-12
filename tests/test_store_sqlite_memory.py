"""SQLite episodic + fact stores (issue #496).

Covers persistence across re-instantiation, schema versioning, shared-file
usage, closed-store semantics, and search/order parity with the in-memory
backends (so swapping backends does not change context-build output).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from contextweaver.exceptions import ItemNotFoundError, StoreClosedError
from contextweaver.store._sqlite_base import schema_version
from contextweaver.store.episodic import Episode, InMemoryEpisodicStore
from contextweaver.store.facts import Fact, InMemoryFactStore
from contextweaver.store.sqlite_episodic import MIGRATIONS as EP_MIGRATIONS
from contextweaver.store.sqlite_episodic import VERSION_TABLE as EP_VERSION_TABLE
from contextweaver.store.sqlite_episodic import SqliteEpisodicStore
from contextweaver.store.sqlite_event_log import SqliteEventLog
from contextweaver.store.sqlite_facts import MIGRATIONS as FACT_MIGRATIONS
from contextweaver.store.sqlite_facts import VERSION_TABLE as FACT_VERSION_TABLE
from contextweaver.store.sqlite_facts import SqliteFactStore

# ---------------------------------------------------------------------------
# Lifecycle / migrations
# ---------------------------------------------------------------------------


def test_episodic_open_creates_file_and_migrations(tmp_path: Path) -> None:
    store = SqliteEpisodicStore(tmp_path / "nested" / "mem.db")
    assert (tmp_path / "nested" / "mem.db").is_file()
    assert schema_version(store._require_conn(), EP_VERSION_TABLE) == len(EP_MIGRATIONS) - 1
    store.close()


def test_fact_open_creates_file_and_migrations(tmp_path: Path) -> None:
    store = SqliteFactStore(tmp_path / "mem.db")
    assert schema_version(store._require_conn(), FACT_VERSION_TABLE) == len(FACT_MIGRATIONS) - 1
    store.close()


def test_closed_stores_raise(tmp_path: Path) -> None:
    ep = SqliteEpisodicStore(tmp_path / "a.db")
    ep.close()
    ep.close()  # idempotent
    with pytest.raises(StoreClosedError):
        ep.all()

    fa = SqliteFactStore(tmp_path / "b.db")
    fa.close()
    with pytest.raises(StoreClosedError):
        fa.all()


def test_context_manager_closes(tmp_path: Path) -> None:
    with SqliteFactStore(tmp_path / "c.db") as store:
        store.put(Fact(fact_id="f1", key="env", value="prod"))
        assert store.get("f1").value == "prod"
    with pytest.raises(StoreClosedError):
        store.all()


# ---------------------------------------------------------------------------
# Persistence across re-instantiation
# ---------------------------------------------------------------------------


def test_episodic_persists_across_reopen(tmp_path: Path) -> None:
    db = tmp_path / "mem.db"
    store = SqliteEpisodicStore(db)
    store.add(Episode(episode_id="e1", summary="deployed", tags=["ops"], metadata={"n": 1}))
    store.add(Episode(episode_id="e2", summary="rotated credentials"))
    store.close()

    reopened = SqliteEpisodicStore(db)
    try:
        assert [e.episode_id for e in reopened.all()] == ["e1", "e2"]
        e1 = reopened.get("e1")
        assert e1 is not None
        assert e1.tags == ["ops"] and e1.metadata == {"n": 1}
        assert reopened.latest(1)[0][0] == "e2"
    finally:
        reopened.close()


def test_fact_persists_and_upserts_across_reopen(tmp_path: Path) -> None:
    db = tmp_path / "mem.db"
    store = SqliteFactStore(db)
    store.put(Fact(fact_id="f1", key="env", value="prod", tags=["a"]))
    store.put(Fact(fact_id="f2", key="region", value="eu"))
    store.close()

    reopened = SqliteFactStore(db)
    try:
        reopened.put(Fact(fact_id="f1", key="env", value="staging"))  # upsert survives reopen
        assert reopened.get("f1").value == "staging"
        assert [f.fact_id for f in reopened.all()] == ["f1", "f2"]
        assert reopened.list_keys() == ["env", "region"]
    finally:
        reopened.close()


def test_shared_database_file_for_all_sqlite_stores(tmp_path: Path) -> None:
    """Event log + episodic + facts coexist in one file (distinct tables)."""
    db = tmp_path / "agent.db"
    events = SqliteEventLog(db)
    episodic = SqliteEpisodicStore(db)
    facts = SqliteFactStore(db)
    try:
        episodic.add(Episode(episode_id="e1", summary="hello"))
        facts.put(Fact(fact_id="f1", key="k", value="v"))
        assert episodic.get("e1") is not None
        assert facts.get("f1").value == "v"
        assert events.count() == 0
    finally:
        events.close()
        episodic.close()
        facts.close()


def test_list_keys_prefix_escapes_wildcards(tmp_path: Path) -> None:
    store = SqliteFactStore(":memory:")
    store.put(Fact(fact_id="f1", key="a%b", value="1"))
    store.put(Fact(fact_id="f2", key="axb", value="2"))
    # The '%' must be treated literally, not as a LIKE wildcard.
    assert store.list_keys(prefix="a%") == ["a%b"]
    store.close()


# ---------------------------------------------------------------------------
# Parity with the in-memory backends
# ---------------------------------------------------------------------------


def test_episodic_search_matches_in_memory() -> None:
    episodes = [
        Episode(episode_id="e1", summary="deployed the billing service to production"),
        Episode(episode_id="e2", summary="rotated the database credentials for staging"),
        Episode(episode_id="e3", summary="investigated a billing latency regression"),
    ]
    mem = InMemoryEpisodicStore()
    sql = SqliteEpisodicStore(":memory:")
    try:
        for ep in episodes:
            mem.add(ep)
            sql.add(ep)
        for query in ("billing production", "database credentials", "latency"):
            mem_ids = [e.episode_id for e in mem.search(query, top_k=2)]
            sql_ids = [e.episode_id for e in sql.search(query, top_k=2)]
            assert mem_ids == sql_ids, query
    finally:
        sql.close()


def test_fact_ordering_matches_in_memory() -> None:
    facts = [
        Fact(fact_id="f3", key="region", value="eu"),
        Fact(fact_id="f1", key="env", value="prod"),
        Fact(fact_id="f2", key="env", value="dev"),
    ]
    mem = InMemoryFactStore()
    sql = SqliteFactStore(":memory:")
    try:
        for fact in facts:
            mem.put(fact)
            sql.put(fact)
        assert [f.fact_id for f in mem.all()] == [f.fact_id for f in sql.all()]
        assert [f.fact_id for f in mem.get_by_key("env")] == [
            f.fact_id for f in sql.get_by_key("env")
        ]
        assert mem.list_keys() == sql.list_keys()
    finally:
        sql.close()


def test_episodic_delete_missing_raises() -> None:
    store = SqliteEpisodicStore(":memory:")
    with pytest.raises(ItemNotFoundError):
        store.delete("nope")
    store.close()

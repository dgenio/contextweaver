"""Run the store-protocol conformance kit against every bundled backend (#520).

This both proves the in-memory, JSON-file, and SQLite backends conform and
exercises the kit itself, so a regression in either surfaces here.
"""

from __future__ import annotations

from pathlib import Path

from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.store.episodic import InMemoryEpisodicStore
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.store.facts import InMemoryFactStore
from contextweaver.store.json_file_artifacts import JsonFileArtifactStore
from contextweaver.store.sqlite_episodic import SqliteEpisodicStore
from contextweaver.store.sqlite_event_log import SqliteEventLog
from contextweaver.store.sqlite_facts import SqliteFactStore
from contextweaver.store.testing import (
    check_artifact_store_conformance,
    check_episodic_store_conformance,
    check_event_log_conformance,
    check_fact_store_conformance,
)


def test_in_memory_event_log_conformance() -> None:
    check_event_log_conformance(InMemoryEventLog)


def test_sqlite_event_log_conformance() -> None:
    check_event_log_conformance(lambda: SqliteEventLog(":memory:"))


def test_in_memory_artifact_store_conformance() -> None:
    check_artifact_store_conformance(InMemoryArtifactStore)


def test_json_file_artifact_store_conformance(tmp_path: Path) -> None:
    counter = {"n": 0}

    def make_store() -> JsonFileArtifactStore:
        # A fresh subdirectory per call so the factory yields an empty store.
        counter["n"] += 1
        return JsonFileArtifactStore(tmp_path / f"store{counter['n']}")

    check_artifact_store_conformance(make_store)


def test_in_memory_episodic_store_conformance() -> None:
    check_episodic_store_conformance(InMemoryEpisodicStore)


def test_sqlite_episodic_store_conformance() -> None:
    check_episodic_store_conformance(lambda: SqliteEpisodicStore(":memory:"))


def test_in_memory_fact_store_conformance() -> None:
    check_fact_store_conformance(InMemoryFactStore)


def test_sqlite_fact_store_conformance() -> None:
    check_fact_store_conformance(lambda: SqliteFactStore(":memory:"))

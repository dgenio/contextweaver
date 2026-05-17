"""Tests for contextweaver.extras.memory.mem0 (issue #195).

The functional tests run only when the ``[mem0]`` extra is installed.
Mem0's network-touching methods (``add`` runs an LLM extraction by
default; ``search`` calls an embedder) are stubbed via
``unittest.mock.MagicMock(spec=Memory)`` so the tests are deterministic
and offline — the spec ensures the mock rejects any call signature
that does not exist on the real :class:`mem0.Memory` class.

One test always runs: it asserts the friendly ``ImportError`` surfaces
when ``[mem0]`` is missing.  The two paths together cover both branches
without requiring ``mem0ai`` in the default CI install.
"""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import pytest

from contextweaver.exceptions import ItemNotFoundError


def _mem0_available() -> bool:
    try:
        importlib.import_module("mem0")
    except ImportError:
        return False
    return True


HAS_MEM0 = _mem0_available()


# ---------------------------------------------------------------------------
# Import-error path (always runs — covers the no-extra case)
# ---------------------------------------------------------------------------


def test_import_error_message_when_extra_missing() -> None:
    """If ``mem0`` is missing, importing the adapter must guide the user."""
    if HAS_MEM0:
        pytest.skip("mem0 is installed; ImportError path not exercised here")
    with pytest.raises(ImportError, match=r"\[mem0\]"):
        importlib.import_module("contextweaver.extras.memory.mem0")


# ---------------------------------------------------------------------------
# Functional tests (run only with the [mem0] extra installed)
# ---------------------------------------------------------------------------


if HAS_MEM0:  # pragma: no cover - exercised only with [mem0]
    from mem0 import Memory

    from contextweaver.extras.memory.mem0 import (
        Mem0BackendError,
        Mem0EpisodicStore,
        Mem0FactStore,
    )
    from contextweaver.store.episodic import Episode
    from contextweaver.store.facts import Fact


@pytest.fixture()
def fake_memory() -> MagicMock:
    """Return a ``MagicMock(spec=Memory)`` with an in-memory record store.

    The mock honours mem0's 2.x return shape (``{"results": [...]}``)
    so the adapter exercises its real response-normalisation path.
    """
    if not HAS_MEM0:
        pytest.skip("mem0 not installed")
    memory = MagicMock(spec=Memory)
    records: list[dict[str, object]] = []
    counter = {"n": 0}

    def _add(messages: str, **kwargs: object) -> dict[str, object]:
        counter["n"] += 1
        meta = kwargs.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {}
        record: dict[str, object] = {
            "id": f"mem-{counter['n']}",
            "memory": messages,
            "metadata": dict(meta),
            "user_id": kwargs.get("user_id"),
        }
        records.append(record)
        return {"results": [{"event": "ADD", **record}]}

    def _scope(filters: object) -> list[dict[str, object]]:
        user_id = filters.get("user_id") if isinstance(filters, dict) else None
        return [r for r in records if r.get("user_id") == user_id]

    def _get_all(*, filters: object = None, top_k: int = 20, **_: object) -> dict[str, object]:
        return {"results": _scope(filters)[:top_k]}

    def _search(
        query: str, *, top_k: int = 20, filters: object = None, **_: object
    ) -> dict[str, object]:
        scoped = _scope(filters)
        ql = query.lower()
        ranked = [r for r in scoped if ql in str(r.get("memory", "")).lower()]
        return {"results": ranked[:top_k]}

    def _delete(memory_id: str) -> None:
        for i, r in enumerate(records):
            if r["id"] == memory_id:
                records.pop(i)
                return

    memory.add.side_effect = _add
    memory.get_all.side_effect = _get_all
    memory.search.side_effect = _search
    memory.delete.side_effect = _delete
    return memory


# ----- Mem0EpisodicStore -----


def test_episodic_add_invokes_memory_add_with_infer_false(fake_memory: MagicMock) -> None:
    store = Mem0EpisodicStore(fake_memory, user_id="alice")
    store.add(Episode(episode_id="ep-1", summary="Investigated outage on 2025-10-01"))
    fake_memory.add.assert_called_once()
    kwargs = fake_memory.add.call_args.kwargs
    assert kwargs["user_id"] == "alice"
    assert kwargs["infer"] is False
    assert kwargs["metadata"]["cw_episode_id"] == "ep-1"


def test_episodic_get_roundtrips_canonical_fields(fake_memory: MagicMock) -> None:
    store = Mem0EpisodicStore(fake_memory, user_id="alice")
    store.add(
        Episode(
            episode_id="ep-1",
            summary="checked logs",
            tags=["incident", "rca"],
            metadata={"sev": "high"},
        )
    )
    got = store.get("ep-1")
    assert got is not None
    assert got.episode_id == "ep-1"
    assert got.summary == "checked logs"
    assert got.tags == ["incident", "rca"]
    assert got.metadata == {"sev": "high"}


def test_episodic_get_missing_returns_none(fake_memory: MagicMock) -> None:
    store = Mem0EpisodicStore(fake_memory, user_id="alice")
    assert store.get("never-added") is None


def test_episodic_search_filters_to_scope_and_returns_episodes(fake_memory: MagicMock) -> None:
    store_a = Mem0EpisodicStore(fake_memory, user_id="alice")
    store_b = Mem0EpisodicStore(fake_memory, user_id="bob")
    store_a.add(Episode("a1", "alice investigated database outage"))
    store_b.add(Episode("b1", "bob updated database schema"))
    results = store_a.search("database", top_k=5)
    assert [ep.episode_id for ep in results] == ["a1"]


def test_episodic_all_returns_all_records_in_scope(fake_memory: MagicMock) -> None:
    store = Mem0EpisodicStore(fake_memory, user_id="alice")
    store.add(Episode("a1", "first"))
    store.add(Episode("a2", "second"))
    eps = store.all()
    assert [ep.episode_id for ep in eps] == ["a1", "a2"]


def test_episodic_latest_returns_most_recent_first(fake_memory: MagicMock) -> None:
    store = Mem0EpisodicStore(fake_memory, user_id="alice")
    store.add(Episode("a1", "first"))
    store.add(Episode("a2", "second"))
    store.add(Episode("a3", "third"))
    latest = store.latest(n=2)
    assert [t[0] for t in latest] == ["a3", "a2"]


def test_episodic_latest_zero_returns_empty(fake_memory: MagicMock) -> None:
    store = Mem0EpisodicStore(fake_memory, user_id="alice")
    store.add(Episode("a1", "first"))
    assert store.latest(n=0) == []


def test_episodic_delete_calls_memory_delete_with_mem_id(fake_memory: MagicMock) -> None:
    store = Mem0EpisodicStore(fake_memory, user_id="alice")
    store.add(Episode("a1", "first"))
    store.delete("a1")
    assert fake_memory.delete.call_args.args == ("mem-1",)
    assert store.get("a1") is None


def test_episodic_delete_missing_raises(fake_memory: MagicMock) -> None:
    store = Mem0EpisodicStore(fake_memory, user_id="alice")
    with pytest.raises(ItemNotFoundError, match="ep-missing"):
        store.delete("ep-missing")


def test_episodic_requires_user_id() -> None:
    if not HAS_MEM0:
        pytest.skip("mem0 not installed")
    memory = MagicMock(spec=Memory)
    with pytest.raises(Mem0BackendError, match="user_id"):
        Mem0EpisodicStore(memory, user_id="")


def test_episodic_scan_limit_raises_not_implemented(fake_memory: MagicMock) -> None:
    store = Mem0EpisodicStore(fake_memory, user_id="alice", scan_limit=2)
    store.add(Episode("a1", "x"))
    store.add(Episode("a2", "y"))
    # Adding the 3rd pushes the scoped count to exactly the limit; scanning
    # ops must then refuse to proceed rather than truncate silently.
    store.add(Episode("a3", "z"))
    with pytest.raises(NotImplementedError, match="scanning ops are no longer"):
        store.all()


# ----- Mem0FactStore -----


def test_fact_put_and_get(fake_memory: MagicMock) -> None:
    store = Mem0FactStore(fake_memory, user_id="alice")
    store.put(Fact(fact_id="f1", key="user.role", value="admin"))
    got = store.get("f1")
    assert got.fact_id == "f1"
    assert got.key == "user.role"
    assert got.value == "admin"


def test_fact_put_overwrites_existing(fake_memory: MagicMock) -> None:
    store = Mem0FactStore(fake_memory, user_id="alice")
    store.put(Fact(fact_id="f1", key="user.role", value="admin"))
    store.put(Fact(fact_id="f1", key="user.role", value="superadmin"))
    got = store.get("f1")
    assert got.value == "superadmin"
    # Should have called delete for the original record before re-adding.
    assert fake_memory.delete.call_count == 1


def test_fact_get_missing_raises(fake_memory: MagicMock) -> None:
    store = Mem0FactStore(fake_memory, user_id="alice")
    with pytest.raises(ItemNotFoundError, match="never-added"):
        store.get("never-added")


def test_fact_get_by_key_returns_sorted(fake_memory: MagicMock) -> None:
    store = Mem0FactStore(fake_memory, user_id="alice")
    store.put(Fact("f2", "tags.color", "blue"))
    store.put(Fact("f1", "tags.color", "green"))
    by_key = store.get_by_key("tags.color")
    assert [f.fact_id for f in by_key] == ["f1", "f2"]


def test_fact_list_keys_with_prefix(fake_memory: MagicMock) -> None:
    store = Mem0FactStore(fake_memory, user_id="alice")
    store.put(Fact("f1", "user.role", "admin"))
    store.put(Fact("f2", "user.email", "a@b"))
    store.put(Fact("f3", "system.region", "eu"))
    assert store.list_keys(prefix="user.") == ["user.email", "user.role"]
    assert store.list_keys() == ["system.region", "user.email", "user.role"]


def test_fact_delete(fake_memory: MagicMock) -> None:
    store = Mem0FactStore(fake_memory, user_id="alice")
    store.put(Fact("f1", "user.role", "admin"))
    store.delete("f1")
    with pytest.raises(ItemNotFoundError):
        store.get("f1")


def test_fact_all_returns_sorted_by_id(fake_memory: MagicMock) -> None:
    store = Mem0FactStore(fake_memory, user_id="alice")
    store.put(Fact("f2", "k1", "v1"))
    store.put(Fact("f1", "k2", "v2"))
    facts = store.all()
    assert [f.fact_id for f in facts] == ["f1", "f2"]

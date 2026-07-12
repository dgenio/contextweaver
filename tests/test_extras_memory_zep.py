"""Tests for contextweaver.extras.memory.zep (issue #195).

Functional tests run only when the ``[zep]`` extra (``zep-cloud``) is
installed.  Zep's network-touching client is replaced by a small in-memory
fake whose ``graph.add`` / ``graph.episode.get_by_user_id`` /
``graph.episode.delete`` mirror the real SDK's call shape (verified against
``zep-cloud`` 3.x: ``graph.add(*, data, type, user_id)`` writes an episode;
``episode.get_by_user_id(user_id, *, lastn)`` returns ``EpisodeResponse`` with
an ``.episodes`` list of objects carrying ``.uuid_`` / ``.content``).

One test always runs: it asserts the friendly ``ImportError`` surfaces when
``[zep]`` is missing.
"""

from __future__ import annotations

import importlib
import json
from types import SimpleNamespace
from typing import Any

import pytest

from contextweaver.exceptions import ItemNotFoundError

# The adapter is caller-supplies-the-client ("bring your own"), so it imports
# cleanly without the [zep] extra — zep_cloud is only a type hint (issue #751).
# The fake-driven tests below therefore run in gating CI, no longer skipped.
from contextweaver.extras.memory.zep import (
    ZepBackendError,
    ZepEpisodicStore,
    ZepFactStore,
)
from contextweaver.store.episodic import Episode
from contextweaver.store.facts import Fact


def test_adapter_imports_without_zep_cloud_extra() -> None:
    """The adapter module imports cleanly whether or not ``zep_cloud`` is present.

    Decoupling contract (issue #751): the store classes are usable with any
    duck-typed client (see the fakes below), so importing must not hard-require
    the SDK. Formerly this raised ImportError at import; that eager guard blocked
    the fake tests from running in gating CI.
    """
    module = importlib.import_module("contextweaver.extras.memory.zep")
    assert module.ZepEpisodicStore is ZepEpisodicStore
    assert module.ZepFactStore is ZepFactStore


class _FakeEpisodeClient:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._records = records

    def get_by_user_id(self, user_id: str, *, lastn: int | None = None) -> SimpleNamespace:
        scoped = [
            SimpleNamespace(uuid_=r["uuid"], content=r["content"])
            for r in self._records
            if r["user_id"] == user_id
        ]
        return SimpleNamespace(episodes=scoped[:lastn])

    def delete(self, uuid_: str) -> None:
        self._records[:] = [r for r in self._records if r["uuid"] != uuid_]


class _FakeGraphClient:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._records = records
        self._counter = 0
        self.episode = _FakeEpisodeClient(records)

    def add(self, *, data: str, type: str, user_id: str) -> None:  # noqa: A002
        assert type == "json"
        self._counter += 1
        self._records.append({"uuid": f"ep-{self._counter}", "content": data, "user_id": user_id})


class _FakeZep:
    def __init__(self) -> None:
        self.graph = _FakeGraphClient([])


@pytest.fixture()
def client() -> _FakeZep:
    return _FakeZep()


# ----- ZepEpisodicStore -----


def test_episodic_add_get_roundtrip(client: _FakeZep) -> None:
    s = ZepEpisodicStore(client, user_id="alice")
    s.add(Episode("ep-1", "checked logs", tags=["rca"], metadata={"sev": "high"}))
    got = s.get("ep-1")
    assert got is not None
    assert (got.episode_id, got.summary, got.tags, got.metadata) == (
        "ep-1",
        "checked logs",
        ["rca"],
        {"sev": "high"},
    )


def test_episodic_add_uses_json_type_and_scope(client: _FakeZep) -> None:
    s = ZepEpisodicStore(client, user_id="alice")
    s.add(Episode("ep-1", "x"))
    rec = client.graph._records[0]
    assert rec["user_id"] == "alice"
    assert '"cw_kind": "episode"' in rec["content"]


def test_episodic_upsert_deletes_old_record(client: _FakeZep) -> None:
    s = ZepEpisodicStore(client, user_id="alice")
    s.add(Episode("ep-1", "old"))
    s.add(Episode("ep-1", "new"))
    assert s.get("ep-1").summary == "new"
    assert len(client.graph._records) == 1


def test_episodic_get_missing_returns_none(client: _FakeZep) -> None:
    assert ZepEpisodicStore(client, user_id="alice").get("nope") is None


def test_episodic_search_ranks_and_scopes(client: _FakeZep) -> None:
    a = ZepEpisodicStore(client, user_id="alice")
    b = ZepEpisodicStore(client, user_id="bob")
    a.add(Episode("a1", "alice investigated the database"))  # one term
    a.add(Episode("a2", "alice reviewed the database outage report"))  # both terms
    b.add(Episode("b1", "bob updated the database outage runbook"))  # other scope
    results = a.search("database outage", top_k=5)
    # a2 matches both terms, a1 only one; bob's episode must not leak in.
    assert [ep.episode_id for ep in results] == ["a2", "a1"]


def test_episodic_all_and_latest(client: _FakeZep) -> None:
    s = ZepEpisodicStore(client, user_id="alice")
    s.add(Episode("a1", "first"))
    s.add(Episode("a2", "second"))
    s.add(Episode("a3", "third"))
    assert [ep.episode_id for ep in s.all()] == ["a1", "a2", "a3"]
    assert [t[0] for t in s.latest(n=2)] == ["a3", "a2"]
    assert s.latest(n=0) == []


def test_episodic_delete_and_missing(client: _FakeZep) -> None:
    s = ZepEpisodicStore(client, user_id="alice")
    s.add(Episode("a1", "first"))
    s.delete("a1")
    assert s.get("a1") is None
    with pytest.raises(ItemNotFoundError, match="gone"):
        s.delete("gone")


def test_episodic_requires_user_id(client: _FakeZep) -> None:
    with pytest.raises(ZepBackendError, match="user_id"):
        ZepEpisodicStore(client, user_id="")


def _inject_raw(client: _FakeZep, user_id: str, payload: dict[str, Any]) -> None:
    """Append a raw episode (as a stale/foreign Zep deployment might hold)."""
    client.graph._records.append(
        {"uuid": "raw-1", "user_id": user_id, "content": json.dumps(payload)}
    )


def test_episodic_coerces_malformed_tags_and_metadata(client: _FakeZep) -> None:
    """A scanned episode with non-list tags / non-dict metadata must not corrupt rebuild."""
    s = ZepEpisodicStore(client, user_id="alice")
    _inject_raw(
        client,
        "alice",
        {
            "cw_kind": "episode",
            "cw_episode_id": "e-bad",
            "summary": "s",
            "tags": "oops",  # a bare string would otherwise iterate into characters
            "metadata": "nope",  # a non-dict would otherwise raise in dict(...)
        },
    )
    got = s.get("e-bad")
    assert got is not None
    assert got.tags == []
    assert got.metadata == {}
    # latest() rebuilds metadata via the same coercion path.
    assert s.latest(n=1) == [("e-bad", "s", {})]


def test_episodic_scan_limit_raises(client: _FakeZep) -> None:
    s = ZepEpisodicStore(client, user_id="alice", scan_limit=2)
    s.add(Episode("a1", "x"))
    s.add(Episode("a2", "y"))
    s.add(Episode("a3", "z"))  # append-only fallback once scope hits the limit
    with pytest.raises(NotImplementedError, match="scanning ops are no longer"):
        s.all()


# ----- ZepFactStore -----


def test_fact_put_get_and_overwrite(client: _FakeZep) -> None:
    s = ZepFactStore(client, user_id="alice")
    s.put(Fact("f1", "user.role", "admin"))
    assert s.get("f1").value == "admin"
    s.put(Fact("f1", "user.role", "superadmin"))
    assert s.get("f1").value == "superadmin"
    assert len(client.graph._records) == 1


def test_fact_get_missing_raises(client: _FakeZep) -> None:
    with pytest.raises(ItemNotFoundError, match="nope"):
        ZepFactStore(client, user_id="alice").get("nope")


def test_fact_coerces_malformed_tags_and_metadata(client: _FakeZep) -> None:
    """A scanned fact with non-list tags / non-dict metadata must not corrupt rebuild."""
    s = ZepFactStore(client, user_id="alice")
    _inject_raw(
        client,
        "alice",
        {
            "cw_kind": "fact",
            "cw_fact_id": "f-bad",
            "cw_key": "user.role",
            "value": "admin",
            "tags": "oops",
            "metadata": ["not", "a", "dict"],
        },
    )
    got = s.get("f-bad")
    assert (got.fact_id, got.key, got.value) == ("f-bad", "user.role", "admin")
    assert got.tags == []
    assert got.metadata == {}


def test_fact_get_by_key_sorted(client: _FakeZep) -> None:
    s = ZepFactStore(client, user_id="alice")
    s.put(Fact("f2", "tags.color", "blue"))
    s.put(Fact("f1", "tags.color", "green"))
    assert [f.fact_id for f in s.get_by_key("tags.color")] == ["f1", "f2"]


def test_fact_list_keys_with_prefix(client: _FakeZep) -> None:
    s = ZepFactStore(client, user_id="alice")
    s.put(Fact("f1", "user.role", "admin"))
    s.put(Fact("f2", "user.email", "a@b"))
    s.put(Fact("f3", "system.region", "eu"))
    assert s.list_keys(prefix="user.") == ["user.email", "user.role"]
    assert s.list_keys() == ["system.region", "user.email", "user.role"]


def test_fact_delete_and_all(client: _FakeZep) -> None:
    s = ZepFactStore(client, user_id="alice")
    s.put(Fact("f2", "k1", "v1"))
    s.put(Fact("f1", "k2", "v2"))
    assert [f.fact_id for f in s.all()] == ["f1", "f2"]
    s.delete("f1")
    with pytest.raises(ItemNotFoundError):
        s.get("f1")


def test_fact_and_episode_share_scope_without_collision(client: _FakeZep) -> None:
    ep = ZepEpisodicStore(client, user_id="alice")
    fa = ZepFactStore(client, user_id="alice")
    ep.add(Episode("e1", "an episode"))
    fa.put(Fact("f1", "k", "v"))
    assert [e.episode_id for e in ep.all()] == ["e1"]
    assert [f.fact_id for f in fa.all()] == ["f1"]

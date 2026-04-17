"""Tests for contextweaver.store.StoreBundle serialization."""

from __future__ import annotations

from contextweaver.store import (
    InMemoryArtifactStore,
    InMemoryEpisodicStore,
    InMemoryEventLog,
    InMemoryFactStore,
    StoreBundle,
)
from contextweaver.store.episodic import Episode
from contextweaver.store.facts import Fact
from contextweaver.types import ContextItem, ItemKind


def test_from_dict_all_none() -> None:
    bundle = StoreBundle()
    restored = StoreBundle.from_dict(bundle.to_dict())
    assert restored.artifact_store is None
    assert restored.event_log is None
    assert restored.episodic_store is None
    assert restored.fact_store is None


def test_roundtrip_with_all_stores() -> None:
    artifact_store = InMemoryArtifactStore()
    artifact_store.put("h1", b"raw bytes", media_type="text/plain", label="test")

    event_log = InMemoryEventLog()
    event_log.append(ContextItem(id="c1", kind=ItemKind.user_turn, text="hello"))

    episodic_store = InMemoryEpisodicStore()
    episodic_store.add(Episode(episode_id="e1", summary="search db", tags=["db"]))

    fact_store = InMemoryFactStore()
    fact_store.put(Fact(fact_id="f1", key="lang", value="Python", tags=["python"]))

    bundle = StoreBundle(
        artifact_store=artifact_store,
        event_log=event_log,
        episodic_store=episodic_store,
        fact_store=fact_store,
    )

    restored = StoreBundle.from_dict(bundle.to_dict())

    assert restored.artifact_store is not None
    refs = {r.handle: r for r in restored.artifact_store.list_refs()}
    assert "h1" in refs
    assert refs["h1"].media_type == "text/plain"

    assert restored.event_log is not None
    items = restored.event_log.all()
    assert len(items) == 1
    assert items[0].id == "c1"

    assert restored.episodic_store is not None
    ep = restored.episodic_store.get("e1")
    assert ep is not None
    assert ep.tags == ["db"]

    assert restored.fact_store is not None
    facts = restored.fact_store.all()
    assert len(facts) == 1
    assert facts[0].fact_id == "f1"


def test_roundtrip_partial_stores() -> None:
    event_log = InMemoryEventLog()
    event_log.append(ContextItem(id="c1", kind=ItemKind.tool_call, text="call"))

    bundle = StoreBundle(event_log=event_log)
    restored = StoreBundle.from_dict(bundle.to_dict())

    assert restored.event_log is not None
    assert restored.artifact_store is None
    assert restored.episodic_store is None
    assert restored.fact_store is None


def test_to_dict_structure() -> None:
    bundle = StoreBundle()
    d = bundle.to_dict()
    assert set(d.keys()) == {"artifact_store", "event_log", "episodic_store", "fact_store"}
    assert all(v is None for v in d.values())

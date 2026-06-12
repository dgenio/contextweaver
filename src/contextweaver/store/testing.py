"""Store-protocol conformance kit for contextweaver (issue #520).

A reusable, framework-agnostic conformance suite that exercises any
implementation of the four store protocols
(:class:`~contextweaver.store.protocols.EventLog`,
:class:`~contextweaver.store.protocols.ArtifactStore`,
:class:`~contextweaver.store.protocols.EpisodicStore`,
:class:`~contextweaver.store.protocols.FactStore`).  Third-party and future
first-party backends can prove protocol compliance in a few lines::

    from contextweaver.store.testing import check_artifact_store_conformance

    def test_my_backend_conformance():
        check_artifact_store_conformance(lambda: MyArtifactStore(...))

Each ``check_*`` function takes a zero-argument *factory* that returns a fresh,
empty backend and raises :class:`AssertionError` (or the protocol's documented
exception) on the first deviation.  The kit deliberately imports no test
framework, so it ships in the core wheel and is callable from pytest,
``unittest``, or a plain script.

The checks cover the behavioural contract the Context Engine relies on:
round-trip fidelity, ``not-found`` error semantics, ordering guarantees,
raise-on-missing delete semantics, and (for artifacts) the ``content_hash``
stamp the firewall depends on.  They do **not** assert thread-safety — see the
concurrency contract in ``docs/agent-context/architecture.md`` (issue #458).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from contextweaver.exceptions import (
    ArtifactNotFoundError,
    DuplicateItemError,
    ItemNotFoundError,
)
from contextweaver.store.async_protocols import (
    AsyncArtifactStore,
    AsyncEpisodicStore,
    AsyncEventLog,
    AsyncFactStore,
)
from contextweaver.store.episodic import Episode
from contextweaver.store.facts import Fact
from contextweaver.store.protocols import ArtifactStore, EpisodicStore, EventLog, FactStore
from contextweaver.types import ContextItem, ItemKind


def _assert(condition: bool, message: str) -> None:
    """Raise :class:`AssertionError` with *message* when *condition* is false."""
    if not condition:
        raise AssertionError(f"store conformance: {message}")


def _expect_raises(exc: type[BaseException], func: Callable[[], object], what: str) -> None:
    """Assert that calling *func* raises *exc*."""
    try:
        func()
    except exc:
        return
    except BaseException as other:  # noqa: BLE001 - re-raise as a conformance failure
        raise AssertionError(
            f"store conformance: {what} raised {type(other).__name__}, expected {exc.__name__}"
        ) from other
    raise AssertionError(f"store conformance: {what} did not raise {exc.__name__}")


# ---------------------------------------------------------------------------
# EventLog
# ---------------------------------------------------------------------------


def check_event_log_conformance(make_log: Callable[[], EventLog]) -> None:
    """Assert that *make_log* produces a conformant :class:`EventLog`."""
    log = make_log()
    _assert(isinstance(log, EventLog), "factory does not satisfy the EventLog protocol")
    _assert(log.count() == 0 and len(log) == 0, "a fresh log must be empty")

    first = ContextItem(id="i1", kind=ItemKind.user_turn, text="hello")
    second = ContextItem(id="i2", kind=ItemKind.tool_call, text="call", parent_id="i1")
    log.append(first)
    log.append(second)

    _assert(log.count() == 2 and len(log) == 2, "count/len must reflect appended items")
    _assert(log.get("i1").text == "hello", "get must return the appended item")
    _assert([i.id for i in log.all()] == ["i1", "i2"], "all() must preserve insertion order")
    _assert([i.id for i in log.tail(1)] == ["i2"], "tail(n) must return the last n items")
    _assert(
        [i.id for i in log.filter_by_kind(ItemKind.tool_call)] == ["i2"],
        "filter_by_kind must select by kind",
    )
    _assert([i.id for i in log.children("i1")] == ["i2"], "children must follow parent_id")
    parent = log.parent("i2")
    _assert(parent is not None and parent.id == "i1", "parent must resolve parent_id")
    _assert(log.parent("i1") is None, "parent of a root item must be None")

    _expect_raises(ItemNotFoundError, lambda: log.get("missing"), "get(missing)")
    _expect_raises(
        DuplicateItemError,
        lambda: log.append(ContextItem(id="i1", kind=ItemKind.user_turn, text="dup")),
        "append(duplicate id)",
    )


# ---------------------------------------------------------------------------
# ArtifactStore
# ---------------------------------------------------------------------------


def check_artifact_store_conformance(make_store: Callable[[], ArtifactStore]) -> None:
    """Assert that *make_store* produces a conformant :class:`ArtifactStore`."""
    store = make_store()
    _assert(isinstance(store, ArtifactStore), "factory does not satisfy the ArtifactStore protocol")
    _assert(store.list_refs() == [], "a fresh store must have no refs")
    _assert(store.exists("nope") is False, "exists must be False for an unknown handle")

    ref = store.put("h1", b"hello world", media_type="text/plain", label="greeting")
    _assert(ref.handle == "h1", "put must return a ref for the stored handle")
    _assert(ref.size_bytes == 11, "ref.size_bytes must match the content length")
    _assert(
        ref.content_hash == hashlib.sha256(b"hello world").hexdigest(),
        "put must stamp a sha256 content_hash on the returned ref (firewall #190 relies on it)",
    )
    _assert(store.get("h1") == b"hello world", "get must return the stored bytes")
    _assert(store.exists("h1") is True, "exists must be True after put")
    _assert(store.ref("h1").handle == "h1", "ref must return metadata for a stored handle")
    _assert(store.metadata("h1").handle == "h1", "metadata must alias ref")
    _assert([r.handle for r in store.list_refs()] == ["h1"], "list_refs must include the artifact")

    store.put("h2", b"second")
    _assert(
        [r.handle for r in store.list_refs()] == ["h1", "h2"],
        "list_refs must be sorted by handle",
    )

    _assert(store.drilldown("h1", {"type": "head", "chars": 5}) == "hello", "drilldown head")

    store.delete("h1")
    _assert(store.exists("h1") is False, "exists must be False after delete")
    _expect_raises(ArtifactNotFoundError, lambda: store.get("h1"), "get(deleted)")
    _expect_raises(ArtifactNotFoundError, lambda: store.ref("missing"), "ref(missing)")
    _expect_raises(ArtifactNotFoundError, lambda: store.delete("missing"), "delete(missing)")


# ---------------------------------------------------------------------------
# EpisodicStore
# ---------------------------------------------------------------------------


def check_episodic_store_conformance(make_store: Callable[[], EpisodicStore]) -> None:
    """Assert that *make_store* produces a conformant :class:`EpisodicStore`."""
    store = make_store()
    _assert(isinstance(store, EpisodicStore), "factory does not satisfy the EpisodicStore protocol")
    _assert(store.all() == [], "a fresh episodic store must be empty")
    _assert(store.get("missing") is None, "get of an unknown episode must return None")

    store.add(Episode(episode_id="e1", summary="deployed the service", tags=["ops"]))
    store.add(Episode(episode_id="e2", summary="rotated the database credentials"))

    fetched = store.get("e1")
    _assert(fetched is not None, "get must return an added episode")
    _assert(
        fetched is not None and fetched.summary == "deployed the service",
        "get must round-trip the summary",
    )
    _assert({e.episode_id for e in store.all()} == {"e1", "e2"}, "all must return every episode")

    latest = store.latest(1)
    _assert(len(latest) == 1, "latest(n) must return n tuples")
    _assert(latest[0][0] == "e2", "latest must be most-recent first")

    hits = store.search("database credentials", top_k=1)
    _assert(len(hits) <= 1, "search must respect top_k")
    _assert(all(isinstance(h, Episode) for h in hits), "search must return Episode objects")

    store.delete("e1")
    _assert(store.get("e1") is None, "delete must remove the episode")
    _expect_raises(ItemNotFoundError, lambda: store.delete("missing"), "delete(missing)")


# ---------------------------------------------------------------------------
# FactStore
# ---------------------------------------------------------------------------


def check_fact_store_conformance(make_store: Callable[[], FactStore]) -> None:
    """Assert that *make_store* produces a conformant :class:`FactStore`."""
    store = make_store()
    _assert(isinstance(store, FactStore), "factory does not satisfy the FactStore protocol")
    _assert(store.all() == [], "a fresh fact store must be empty")
    _assert(store.list_keys() == [], "a fresh fact store must have no keys")

    store.put(Fact(fact_id="f1", key="env", value="prod"))
    store.put(Fact(fact_id="f2", key="region", value="eu-west-1"))

    _assert(store.get("f1").value == "prod", "get must round-trip the value")
    _assert([f.fact_id for f in store.all()] == ["f1", "f2"], "all must be sorted by fact_id")
    _assert([f.value for f in store.get_by_key("env")] == ["prod"], "get_by_key must select by key")
    _assert(store.list_keys() == ["env", "region"], "list_keys must list distinct keys sorted")
    _assert(store.list_keys(prefix="reg") == ["region"], "list_keys must honour the prefix filter")

    # put is an upsert: writing an existing fact_id replaces it (documented).
    store.put(Fact(fact_id="f1", key="env", value="staging"))
    _assert(store.get("f1").value == "staging", "put must upsert on an existing fact_id")

    store.delete("f1")
    _expect_raises(ItemNotFoundError, lambda: store.get("f1"), "get(deleted)")
    _expect_raises(ItemNotFoundError, lambda: store.delete("missing"), "delete(missing)")


# ---------------------------------------------------------------------------
# Async variants (issue #495)
# ---------------------------------------------------------------------------


async def _aexpect_raises(exc: type[BaseException], coro: Callable[[], object], what: str) -> None:
    """Assert that awaiting *coro()* raises *exc*."""
    try:
        await coro()  # type: ignore[misc]
    except exc:
        return
    except BaseException as other:  # noqa: BLE001 - re-raise as a conformance failure
        raise AssertionError(
            f"store conformance: {what} raised {type(other).__name__}, expected {exc.__name__}"
        ) from other
    raise AssertionError(f"store conformance: {what} did not raise {exc.__name__}")


async def check_async_event_log_conformance(make_log: Callable[[], AsyncEventLog]) -> None:
    """Assert that *make_log* produces a conformant :class:`AsyncEventLog`."""
    log = make_log()
    _assert(isinstance(log, AsyncEventLog), "factory does not satisfy the AsyncEventLog protocol")
    _assert(await log.count() == 0, "a fresh log must be empty")

    first = ContextItem(id="i1", kind=ItemKind.user_turn, text="hello")
    second = ContextItem(id="i2", kind=ItemKind.tool_call, text="call", parent_id="i1")
    await log.append(first)
    await log.append(second)

    _assert(await log.count() == 2, "count must reflect appended items")
    _assert((await log.get("i1")).text == "hello", "get must return the appended item")
    _assert([i.id for i in await log.all()] == ["i1", "i2"], "all() must preserve insertion order")
    _assert([i.id for i in await log.tail(1)] == ["i2"], "tail(n) must return the last n items")
    _assert(
        [i.id for i in await log.filter_by_kind(ItemKind.tool_call)] == ["i2"],
        "filter_by_kind must select by kind",
    )
    _assert([i.id for i in await log.children("i1")] == ["i2"], "children must follow parent_id")
    parent = await log.parent("i2")
    _assert(parent is not None and parent.id == "i1", "parent must resolve parent_id")
    _assert((await log.parent("i1")) is None, "parent of a root item must be None")

    await _aexpect_raises(ItemNotFoundError, lambda: log.get("missing"), "get(missing)")
    await _aexpect_raises(
        DuplicateItemError,
        lambda: log.append(ContextItem(id="i1", kind=ItemKind.user_turn, text="dup")),
        "append(duplicate id)",
    )


async def check_async_artifact_store_conformance(
    make_store: Callable[[], AsyncArtifactStore],
) -> None:
    """Assert that *make_store* produces a conformant :class:`AsyncArtifactStore`."""
    store = make_store()
    _assert(
        isinstance(store, AsyncArtifactStore),
        "factory does not satisfy the AsyncArtifactStore protocol",
    )
    _assert(await store.list_refs() == [], "a fresh store must have no refs")
    _assert(await store.exists("nope") is False, "exists must be False for an unknown handle")

    ref = await store.put("h1", b"hello world", media_type="text/plain", label="greeting")
    _assert(ref.handle == "h1", "put must return a ref for the stored handle")
    _assert(
        ref.content_hash == hashlib.sha256(b"hello world").hexdigest(),
        "put must stamp a sha256 content_hash on the returned ref (firewall #190 relies on it)",
    )
    _assert(await store.get("h1") == b"hello world", "get must return the stored bytes")
    _assert(await store.exists("h1") is True, "exists must be True after put")
    _assert([r.handle for r in await store.list_refs()] == ["h1"], "list_refs must include it")
    _assert(await store.drilldown("h1", {"type": "head", "chars": 5}) == "hello", "drilldown head")

    await store.delete("h1")
    _assert(await store.exists("h1") is False, "exists must be False after delete")
    await _aexpect_raises(ArtifactNotFoundError, lambda: store.get("h1"), "get(deleted)")
    await _aexpect_raises(ArtifactNotFoundError, lambda: store.ref("missing"), "ref(missing)")


async def check_async_episodic_store_conformance(
    make_store: Callable[[], AsyncEpisodicStore],
) -> None:
    """Assert that *make_store* produces a conformant :class:`AsyncEpisodicStore`."""
    store = make_store()
    _assert(
        isinstance(store, AsyncEpisodicStore),
        "factory does not satisfy the AsyncEpisodicStore protocol",
    )
    _assert(await store.all() == [], "a fresh episodic store must be empty")
    _assert(await store.get("missing") is None, "get of an unknown episode must return None")

    await store.add(Episode(episode_id="e1", summary="deployed the service", tags=["ops"]))
    await store.add(Episode(episode_id="e2", summary="rotated the database credentials"))

    fetched = await store.get("e1")
    _assert(fetched is not None and fetched.summary == "deployed the service", "get round-trip")
    _assert({e.episode_id for e in await store.all()} == {"e1", "e2"}, "all must return every one")
    latest = await store.latest(1)
    _assert(len(latest) == 1 and latest[0][0] == "e2", "latest must be most-recent first")
    hits = await store.search("database credentials", top_k=1)
    _assert(len(hits) <= 1, "search must respect top_k")

    await store.delete("e1")
    _assert(await store.get("e1") is None, "delete must remove the episode")
    await _aexpect_raises(ItemNotFoundError, lambda: store.delete("missing"), "delete(missing)")


async def check_async_fact_store_conformance(make_store: Callable[[], AsyncFactStore]) -> None:
    """Assert that *make_store* produces a conformant :class:`AsyncFactStore`."""
    store = make_store()
    _assert(
        isinstance(store, AsyncFactStore), "factory does not satisfy the AsyncFactStore protocol"
    )
    _assert(await store.all() == [], "a fresh fact store must be empty")

    await store.put(Fact(fact_id="f1", key="env", value="prod"))
    await store.put(Fact(fact_id="f2", key="region", value="eu-west-1"))

    _assert((await store.get("f1")).value == "prod", "get must round-trip the value")
    _assert([f.fact_id for f in await store.all()] == ["f1", "f2"], "all must be sorted by fact_id")
    _assert([f.value for f in await store.get_by_key("env")] == ["prod"], "get_by_key selects")
    _assert(await store.list_keys() == ["env", "region"], "list_keys lists distinct keys sorted")

    await store.put(Fact(fact_id="f1", key="env", value="staging"))
    _assert((await store.get("f1")).value == "staging", "put must upsert on an existing fact_id")

    await store.delete("f1")
    await _aexpect_raises(ItemNotFoundError, lambda: store.get("f1"), "get(deleted)")
    await _aexpect_raises(ItemNotFoundError, lambda: store.delete("missing"), "delete(missing)")

"""Tests for the routing fitted-index cache (issues #543 / #624 / #685).

Covers the content fingerprint, the two-layer :class:`RoutingIndexCache`
(in-process + on-disk), and the :class:`CachedRetriever` wrapper: cold-fit /
warm-load behaviour, cross-instance and cross-"process" reuse, transparency
(byte-identical scores), and resilience to corrupt or incompatible payloads.
"""

from __future__ import annotations

import json
from pathlib import Path

from contextweaver.routing.catalog import generate_sample_catalog, load_catalog_dicts
from contextweaver.routing.index_cache import (
    CACHE_ENVELOPE_VERSION,
    CachedRetriever,
    RoutingIndexCache,
    index_fingerprint,
)
from contextweaver.routing.registry import TfIdfRetriever
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import SelectableItem

_QUERIES = [
    "create a new crm deal",
    "list dashboards",
    "export the audit log",
    "send a notification",
]


def _catalog(n: int = 60, seed: int = 7) -> list[SelectableItem]:
    return load_catalog_dicts(generate_sample_catalog(n=n, seed=seed))


# ---------------------------------------------------------------------------
# index_fingerprint
# ---------------------------------------------------------------------------


def test_fingerprint_is_deterministic() -> None:
    docs = ["alpha tool", "beta tool", "gamma tool"]
    assert index_fingerprint(docs, engine_name="tfidf") == index_fingerprint(
        docs, engine_name="tfidf"
    )


def test_fingerprint_is_order_sensitive() -> None:
    a = index_fingerprint(["alpha", "beta"], engine_name="tfidf")
    b = index_fingerprint(["beta", "alpha"], engine_name="tfidf")
    assert a != b


def test_fingerprint_separates_engines() -> None:
    docs = ["alpha", "beta"]
    assert index_fingerprint(docs, engine_name="tfidf") != index_fingerprint(
        docs, engine_name="bm25"
    )


def test_fingerprint_no_delimiter_collision() -> None:
    # Two docs vs one concatenated doc must not collide (length + NUL framing).
    assert index_fingerprint(["a", "b"], engine_name="e") != index_fingerprint(
        ["a\x00b"], engine_name="e"
    )


# ---------------------------------------------------------------------------
# RoutingIndexCache — in-process layer
# ---------------------------------------------------------------------------


def test_cache_in_memory_hit_and_miss() -> None:
    cache = RoutingIndexCache()
    assert cache.get("missing") is None
    assert cache.misses == 1
    cache.put("fp", {"state": {"x": 1}})
    assert cache.get("fp") == {"state": {"x": 1}}
    assert cache.hits == 1


def test_cache_lru_eviction() -> None:
    cache = RoutingIndexCache(max_entries=2)
    cache.put("a", {"v": 1})
    cache.put("b", {"v": 2})
    cache.get("a")  # touch a so b is the LRU
    cache.put("c", {"v": 3})  # evicts b
    assert cache.get("b") is None
    assert cache.get("a") == {"v": 1}
    assert cache.get("c") == {"v": 3}


# ---------------------------------------------------------------------------
# RoutingIndexCache — on-disk layer
# ---------------------------------------------------------------------------


def test_cache_disk_persists_across_instances(tmp_path: Path) -> None:
    first = RoutingIndexCache(directory=tmp_path)
    first.put("fp1", {"envelope_version": CACHE_ENVELOPE_VERSION, "state": {"k": "v"}})
    # A brand-new cache object over the same directory simulates a new process.
    second = RoutingIndexCache(directory=tmp_path)
    assert second.get("fp1") == {"envelope_version": CACHE_ENVELOPE_VERSION, "state": {"k": "v"}}
    assert second.hits == 1


def test_cache_disk_payload_is_deterministic_json(tmp_path: Path) -> None:
    cache = RoutingIndexCache(directory=tmp_path)
    cache.put("fp", {"b": 2, "a": 1})
    written = (tmp_path / "idx_fp.json").read_text(encoding="utf-8")
    # sort_keys + compact separators → stable bytes for drift-friendly diffs.
    assert written == '{"a":1,"b":2}'


def test_cache_corrupt_disk_entry_is_a_miss(tmp_path: Path) -> None:
    (tmp_path / "idx_bad.json").write_text("{ not json", encoding="utf-8")
    cache = RoutingIndexCache(directory=tmp_path)
    assert cache.get("bad") is None
    assert cache.misses == 1


def test_cache_non_object_disk_entry_is_a_miss(tmp_path: Path) -> None:
    (tmp_path / "idx_arr.json").write_text("[1, 2, 3]", encoding="utf-8")
    cache = RoutingIndexCache(directory=tmp_path)
    assert cache.get("arr") is None


# ---------------------------------------------------------------------------
# CachedRetriever — fit / load / delegation
# ---------------------------------------------------------------------------


def test_cached_retriever_cold_then_warm_in_process() -> None:
    cache = RoutingIndexCache()
    docs = [f"tool number {i} does work" for i in range(20)]

    cold = CachedRetriever(TfIdfRetriever(), cache)
    cold.fit(docs)
    assert cold.loaded_from_cache is False
    assert cache.misses == 1

    warm = CachedRetriever(TfIdfRetriever(), cache)
    warm.fit(docs)
    assert warm.loaded_from_cache is True
    assert cache.hits == 1


def test_cached_retriever_warm_load_from_disk(tmp_path: Path) -> None:
    docs = [f"alpha beta {i}" for i in range(15)]
    CachedRetriever(TfIdfRetriever(), RoutingIndexCache(directory=tmp_path)).fit(docs)

    fresh = CachedRetriever(TfIdfRetriever(), RoutingIndexCache(directory=tmp_path))
    fresh.fit(docs)
    assert fresh.loaded_from_cache is True
    # Restored index scores identically to a freshly fitted one.
    plain = TfIdfRetriever()
    plain.fit(docs)
    assert fresh.search("alpha", 5) == plain.search("alpha", 5)
    assert fresh.score_one("beta", 0) == plain.score_one("beta", 0)


def test_cached_retriever_incompatible_version_refits(tmp_path: Path) -> None:
    docs = ["one two", "three four"]
    fp = index_fingerprint(docs, engine_name="tfidf")
    cache = RoutingIndexCache(directory=tmp_path)
    # Pre-seed a payload from a future, unknown codec version.
    cache.put(fp, {"envelope_version": 999, "codec": "tfidf", "version": 999, "state": {}})

    retr = CachedRetriever(TfIdfRetriever(), cache)
    retr.fit(docs)
    assert retr.loaded_from_cache is False  # mismatched version ⇒ re-fit
    assert retr.search("one", 2)  # still usable


def test_cached_retriever_round_trip_scores_match_baseline() -> None:
    docs = [f"namespace.tool_{i} performs action {i}" for i in range(30)]
    cache = RoutingIndexCache()
    cached = CachedRetriever(TfIdfRetriever(), cache)
    cached.fit(docs)  # cold store
    reloaded = CachedRetriever(TfIdfRetriever(), cache)
    reloaded.fit(docs)  # warm load

    plain = TfIdfRetriever()
    plain.fit(docs)
    for q in ("action 3", "namespace tool", "performs"):
        assert reloaded.search(q, 10) == plain.search(q, 10)


# ---------------------------------------------------------------------------
# End-to-end with Router
# ---------------------------------------------------------------------------


def test_router_with_cached_retriever_matches_plain_router(tmp_path: Path) -> None:
    items = _catalog()
    graph = TreeBuilder().build(items)

    plain = Router(graph, items=items, retriever=TfIdfRetriever())
    cache = RoutingIndexCache(directory=tmp_path)
    Router(graph, items=items, retriever=CachedRetriever(TfIdfRetriever(), cache)).route(
        _QUERIES[0]
    )

    # New router + new cache instance over the same dir = warm load (new process).
    warm_retriever = CachedRetriever(TfIdfRetriever(), RoutingIndexCache(directory=tmp_path))
    warm = Router(graph, items=items, retriever=warm_retriever)

    for q in _QUERIES:
        base = plain.route(q)
        got = warm.route(q)
        assert got.candidate_ids == base.candidate_ids
        assert got.scores == base.scores
    assert warm_retriever.loaded_from_cache is True


def test_disk_envelope_carries_expected_shape(tmp_path: Path) -> None:
    docs = ["one", "two", "three"]
    CachedRetriever(TfIdfRetriever(), RoutingIndexCache(directory=tmp_path)).fit(docs)
    files = list(tmp_path.glob("idx_*.json"))
    assert len(files) == 1
    envelope = json.loads(files[0].read_text(encoding="utf-8"))
    assert envelope["codec"] == "tfidf"
    assert envelope["envelope_version"] == CACHE_ENVELOPE_VERSION
    assert {"documents", "idf", "corpus_size"} <= set(envelope["state"])

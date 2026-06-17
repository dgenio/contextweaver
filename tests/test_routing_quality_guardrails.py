"""Routing-scale quality guardrails (issue #686).

These tests pin the quality contract that the index/cache optimization work
(issues #543 / #624 / #685) must not regress:

1. **Transparency** — routing through a :class:`CachedRetriever` (cold *or*
   warm-loaded from disk) produces byte-identical candidate ids and scores to
   routing through a plain retriever, across the whole gold set.  This is the
   primary guard: an optimization that changes routing results fails here.
2. **Recall floor** — mean recall@5 over the gold set stays at or above a
   committed floor, so a future change that silently degrades retrieval
   quality is caught even if it stays internally consistent.

The recall floor is set conservatively below the measured deterministic
baseline; it is a regression tripwire, not a quality target.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contextweaver.eval.metrics import recall_at_k
from contextweaver.routing.catalog import generate_sample_catalog, load_catalog_dicts
from contextweaver.routing.index_cache import CachedRetriever, RoutingIndexCache
from contextweaver.routing.registry import TfIdfRetriever
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import SelectableItem

_GOLD_PATH = Path(__file__).resolve().parent.parent / "benchmarks" / "routing_gold.json"

# Measured deterministic TF-IDF baseline on the natural 83-item pool over all
# 200 gold queries is recall@5 = 0.3825 (mrr 0.3242).  The floor sits just
# below it so genuine regressions trip while leaving room for benign gold-set
# growth; raise it deliberately if the baseline improves.
_RECALL_AT_5_FLOOR = 0.37


def _gold() -> list[dict[str, object]]:
    return json.loads(_GOLD_PATH.read_text(encoding="utf-8"))


def _catalog() -> list[SelectableItem]:
    return load_catalog_dicts(generate_sample_catalog(n=83, seed=42))


def test_recall_at_5_meets_committed_floor() -> None:
    items = _catalog()
    ids = {it.id for it in items}
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items, retriever=TfIdfRetriever(), top_k=10)

    recalls: list[float] = []
    for entry in _gold():
        expected = [e for e in entry["expected"] if e in ids]  # type: ignore[union-attr]
        if not expected:
            continue
        result = router.route(str(entry["query"]))
        recalls.append(recall_at_k(result.candidate_ids, expected, 5))

    assert recalls, "gold set produced no evaluable queries"
    mean_recall = sum(recalls) / len(recalls)
    assert mean_recall >= _RECALL_AT_5_FLOOR, (
        f"recall@5 {mean_recall:.4f} fell below floor {_RECALL_AT_5_FLOOR}"
    )


@pytest.mark.parametrize("from_disk", [False, True])
def test_cached_routing_is_transparent_over_gold_set(from_disk: bool, tmp_path: Path) -> None:
    items = _catalog()
    graph = TreeBuilder().build(items)
    queries = [str(e["query"]) for e in _gold()]

    plain = Router(graph, items=items, retriever=TfIdfRetriever(), top_k=10)

    directory = tmp_path if from_disk else None
    cache = RoutingIndexCache(directory=directory)
    # Warm the cache once (cold fit + store).
    Router(graph, items=items, retriever=CachedRetriever(TfIdfRetriever(), cache)).route(queries[0])
    # A fresh cache instance forces a warm load (from disk when from_disk).
    warm_cache = RoutingIndexCache(directory=directory) if from_disk else cache
    warm_retriever = CachedRetriever(TfIdfRetriever(), warm_cache)
    cached_router = Router(graph, items=items, retriever=warm_retriever)

    for query in queries:
        base = plain.route(query)
        got = cached_router.route(query)
        assert got.candidate_ids == base.candidate_ids
        assert got.scores == base.scores

    if from_disk:
        assert warm_retriever.loaded_from_cache is True

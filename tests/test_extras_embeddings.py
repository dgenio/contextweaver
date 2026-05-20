"""Tests for contextweaver.extras.embeddings (issue #8).

Default-install tests use a deterministic mock backend; the real sentence-
transformers integration test is gated behind :func:`pytest.importorskip`.
"""

from __future__ import annotations

import hashlib
import math

import pytest

from contextweaver.exceptions import ConfigError
from contextweaver.extras.embeddings import HashingEmbeddingBackend, HybridEmbeddingRetriever
from contextweaver.protocols import EmbeddingBackend
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import SelectableItem

# ---------------------------------------------------------------------------
# Deterministic mock backend
# ---------------------------------------------------------------------------


class HashEmbeddingBackend:
    """Deterministic stand-in :class:`EmbeddingBackend` for unit tests.

    Embeds each text as a 16-dim L2-normalised vector derived from a SHA-256
    hash of the text.  Reproducible across runs / processes — no model
    download or randomness.
    """

    DIM = 16

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            # Take the first DIM bytes, scale to [-1, 1].
            vec = [(b - 128) / 128.0 for b in digest[: self.DIM]]
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([x / norm for x in vec])
        return out

    def similarity(
        self,
        query_vec: list[float],
        corpus_vecs: list[list[float]],
    ) -> list[float]:
        return [sum(a * b for a, b in zip(query_vec, v, strict=False)) for v in corpus_vecs]


def _item(
    iid: str,
    *,
    name: str | None = None,
    description: str = "desc",
    tags: list[str] | None = None,
    namespace: str = "",
) -> SelectableItem:
    return SelectableItem(
        id=iid,
        kind="tool",
        name=name or iid,
        description=description,
        tags=list(tags or []),
        namespace=namespace,
    )


# ---------------------------------------------------------------------------
# EmbeddingBackend protocol surface
# ---------------------------------------------------------------------------


def test_hash_backend_implements_embedding_backend_protocol() -> None:
    """The mock backend satisfies the public protocol shape used by Router."""
    backend = HashEmbeddingBackend()
    assert isinstance(backend, EmbeddingBackend)


# ---------------------------------------------------------------------------
# HybridEmbeddingRetriever — direct unit tests
# ---------------------------------------------------------------------------


def test_hybrid_retriever_fit_then_score_one_is_in_unit_range_for_normalised_backend() -> None:
    backend = HashEmbeddingBackend()
    retriever = HybridEmbeddingRetriever(backend)
    retriever.fit(["read database", "send email", "search docs"])
    # Hybrid score = 0.7 * cos + 0.3 * tfidf; with unit-norm vectors the cos
    # component is in [-1, 1] and tfidf is non-negative, so the hybrid is
    # bounded.
    s = retriever.score_one("read database", 0)
    assert -1.0 <= s <= 1.0 + 0.3  # +0.3 cap from the tfidf weight


def test_hybrid_retriever_search_returns_top_k_sorted_desc_with_id_tie_break() -> None:
    backend = HashEmbeddingBackend()
    retriever = HybridEmbeddingRetriever(backend)
    retriever.fit(["alpha", "beta", "gamma", "delta", "epsilon"])
    out = retriever.search("alpha", top_k=3)
    assert len(out) == 3
    scores = [score for _, score in out]
    assert scores == sorted(scores, reverse=True)


def test_hybrid_retriever_empty_corpus_returns_empty_search() -> None:
    backend = HashEmbeddingBackend()
    retriever = HybridEmbeddingRetriever(backend)
    retriever.fit([])
    assert retriever.search("query", top_k=5) == []


def test_hybrid_retriever_score_one_returns_zero_for_out_of_range_index() -> None:
    backend = HashEmbeddingBackend()
    retriever = HybridEmbeddingRetriever(backend)
    retriever.fit(["a", "b"])
    assert retriever.score_one("q", -1) == 0.0
    assert retriever.score_one("q", 5) == 0.0


def test_hybrid_retriever_rejects_out_of_range_embedding_weight() -> None:
    backend = HashEmbeddingBackend()
    with pytest.raises(ConfigError, match="embedding_weight"):
        HybridEmbeddingRetriever(backend, embedding_weight=1.5)
    with pytest.raises(ConfigError, match="embedding_weight"):
        HybridEmbeddingRetriever(backend, embedding_weight=-0.1)


def test_hybrid_retriever_score_one_is_deterministic_for_same_query() -> None:
    backend = HashEmbeddingBackend()
    retriever = HybridEmbeddingRetriever(backend)
    retriever.fit(["read database", "send email"])
    a = retriever.score_one("read database", 0)
    b = retriever.score_one("read database", 0)
    assert a == b


# ---------------------------------------------------------------------------
# Router integration with embedding_backend
# ---------------------------------------------------------------------------


def test_router_accepts_embedding_backend_and_routes_successfully() -> None:
    items = [
        _item("db_read", description="Read from the database", tags=["data"]),
        _item("send_email", description="Send an email", tags=["comm"]),
        _item("search_docs", description="Search documentation", tags=["search"]),
    ]
    graph = TreeBuilder().build(items)
    router = Router(
        graph,
        items=items,
        embedding_backend=HashEmbeddingBackend(),
        top_k=3,
    )
    result = router.route("read database")
    assert result.candidate_ids  # at least one match
    assert result.trace.retriever_engine == "embedding+tfidf"


def test_router_rejects_both_retriever_and_embedding_backend() -> None:
    from contextweaver.exceptions import ConfigError
    from contextweaver.routing.registry import TfIdfRetriever

    items = [_item("a"), _item("b")]
    graph = TreeBuilder().build(items)
    with pytest.raises(ConfigError, match="retriever= or embedding_backend="):
        Router(
            graph,
            items=items,
            retriever=TfIdfRetriever(),
            embedding_backend=HashEmbeddingBackend(),
        )


def test_router_without_embedding_backend_falls_back_to_tfidf() -> None:
    items = [_item("a"), _item("b")]
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items)
    result = router.route("anything")
    assert result.trace.retriever_engine == "tfidf"


# ---------------------------------------------------------------------------
# HashingEmbeddingBackend — stdlib-only baseline (issue #266)
# ---------------------------------------------------------------------------


def test_hashing_backend_implements_embedding_backend_protocol() -> None:
    backend = HashingEmbeddingBackend()
    assert isinstance(backend, EmbeddingBackend)


def test_hashing_backend_emits_l2_normalised_vectors() -> None:
    backend = HashingEmbeddingBackend(n_features=64)
    [vec] = backend.embed(["send a notification to the infra channel"])
    assert len(vec) == 64
    norm = math.sqrt(sum(x * x for x in vec))
    assert norm == pytest.approx(1.0, abs=1e-9)


def test_hashing_backend_empty_input_returns_empty_list() -> None:
    assert HashingEmbeddingBackend().embed([]) == []


def test_hashing_backend_empty_text_returns_zero_vector() -> None:
    [vec] = HashingEmbeddingBackend(n_features=32).embed([""])
    assert vec == [0.0] * 32


def test_hashing_backend_is_deterministic_across_calls() -> None:
    a = HashingEmbeddingBackend(n_features=128).embed(["search records"])
    b = HashingEmbeddingBackend(n_features=128).embed(["search records"])
    assert a == b


def test_hashing_backend_seed_changes_projection() -> None:
    a = HashingEmbeddingBackend(n_features=128, seed=0).embed(["search records"])[0]
    b = HashingEmbeddingBackend(n_features=128, seed=7).embed(["search records"])[0]
    assert a != b


def test_hashing_backend_similarity_is_dot_product_on_unit_vectors() -> None:
    backend = HashingEmbeddingBackend(n_features=64)
    vecs = backend.embed(["send a notification", "send a notification"])
    sims = backend.similarity(vecs[0], vecs)
    assert sims[0] == pytest.approx(1.0, abs=1e-9)
    assert sims[1] == pytest.approx(1.0, abs=1e-9)


def test_hashing_backend_self_similarity_dominates_unrelated() -> None:
    backend = HashingEmbeddingBackend()
    vecs = backend.embed(
        [
            "send a deployment freeze notification to engineers",
            "render a quarterly analytics dashboard for finance",
        ]
    )
    self_sim = backend.similarity(vecs[0], [vecs[0]])[0]
    cross_sim = backend.similarity(vecs[0], [vecs[1]])[0]
    assert self_sim == pytest.approx(1.0, abs=1e-9)
    assert self_sim > cross_sim


def test_hashing_backend_rejects_invalid_n_features() -> None:
    with pytest.raises(ConfigError):
        HashingEmbeddingBackend(n_features=0)


def test_hashing_backend_rejects_invalid_ngram_range() -> None:
    with pytest.raises(ConfigError):
        HashingEmbeddingBackend(ngram_range=(5, 3))


def test_hashing_backend_routes_via_router_embedding_kwarg() -> None:
    items = [
        _item("notifications.send", description="Send a notification to a channel"),
        _item("analytics.query", description="Query the analytics warehouse"),
    ]
    graph = TreeBuilder().build(items)
    backend = HashingEmbeddingBackend()
    router = Router(graph, items=items, embedding_backend=backend, top_k=2)
    result = router.route("notify the channel")
    assert result.candidate_ids[0] == "notifications.send"


# ---------------------------------------------------------------------------
# Real sentence-transformers integration (skipped when extra not installed)
# ---------------------------------------------------------------------------


def test_sentence_transformer_backend_real_model_round_trip() -> None:
    """End-to-end sanity check with the real sentence-transformers library."""
    pytest.importorskip("sentence_transformers")
    from contextweaver.extras.embeddings import SentenceTransformerBackend

    backend = SentenceTransformerBackend("all-MiniLM-L6-v2")
    vecs = backend.embed(["read database", "send email"])
    assert len(vecs) == 2
    assert all(len(v) == len(vecs[0]) for v in vecs)
    # Self-similarity dominates cross-similarity
    sims_self = backend.similarity(vecs[0], [vecs[0]])
    sims_cross = backend.similarity(vecs[0], [vecs[1]])
    assert sims_self[0] > sims_cross[0]


def test_sentence_transformer_backend_top_k_membership_for_known_query() -> None:
    """The hybrid retriever returns the lexically + semantically closest item first."""
    pytest.importorskip("sentence_transformers")
    from contextweaver.extras.embeddings import SentenceTransformerBackend

    items = [
        _item("schedule_meeting", description="Schedule a meeting on the calendar"),
        _item("send_email", description="Send an email message"),
        _item("create_invoice", description="Create a new invoice for billing"),
    ]
    graph = TreeBuilder().build(items)
    backend = SentenceTransformerBackend("all-MiniLM-L6-v2")
    router = Router(graph, items=items, embedding_backend=backend, top_k=3)
    result = router.route("book a calendar event")
    assert "schedule_meeting" in result.candidate_ids

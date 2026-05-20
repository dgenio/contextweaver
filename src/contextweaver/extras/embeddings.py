"""Optional embedding-based retrieval backend (issue #8).

Gated behind the ``contextweaver[embeddings]`` extra:

.. code-block:: bash

    pip install 'contextweaver[embeddings]'

The default install never imports this module; it is loaded lazily by
:class:`~contextweaver.routing.router.Router` only when an
``embedding_backend=`` argument is supplied.  Importing this module
succeeds without ``sentence-transformers``; only instantiating
:class:`SentenceTransformerBackend` raises ``ImportError`` with the
exact install hint above — matching the convention used by
:mod:`contextweaver.extras.otel`.

What is shipped here:

- :class:`SentenceTransformerBackend` — concrete
  :class:`~contextweaver.protocols.EmbeddingBackend` implementation backed
  by `sentence-transformers <https://www.sbert.net/>`_.
- :class:`HashingEmbeddingBackend` — stdlib-only deterministic baseline
  :class:`~contextweaver.protocols.EmbeddingBackend` using the hashing
  trick (no extras required).  Useful when a stable, dependency-free
  reference is needed (CI baselines, benchmark scorecard rows — #266).
- :class:`HybridEmbeddingRetriever` — :class:`~contextweaver.protocols.Retriever`
  adapter that uses an embedding backend for primary scoring and the
  in-tree TF-IDF scorer as a secondary lexical signal (weighted sum).

Determinism note: embedding inference may be non-deterministic across
hardware (CPU vs GPU, model version, BLAS backend).  When the routing
engine is configured with an :class:`~contextweaver.protocols.EmbeddingBackend`,
the deterministic-by-default guarantee shifts from the engine to the
backend; pin the model version + an embedding cache for byte-exact
reproducibility.

Privacy: this module never sends item bodies over the network — the
sentence-transformers backend loads weights locally and embeds in-process.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from contextweaver._utils import TfIdfScorer, tokenize

if TYPE_CHECKING:
    from contextweaver.protocols import EmbeddingBackend


_INSTALL_HINT = (
    "contextweaver.extras.embeddings requires the [embeddings] extra: "
    "pip install 'contextweaver[embeddings]'"
)


try:  # pragma: no cover - exercised in the integration test path
    from sentence_transformers import SentenceTransformer
except ImportError:  # pragma: no cover - exercised in the default-install path
    SentenceTransformer = None


class SentenceTransformerBackend:
    """:class:`~contextweaver.protocols.EmbeddingBackend` backed by sentence-transformers.

    Args:
        model_name: Hugging Face model id.  ``"all-MiniLM-L6-v2"`` is the
            default — a small (~80 MB), fast, and broadly competitive
            English model.
        normalize_embeddings: When ``True`` (default), the underlying
            model emits unit-norm vectors so :meth:`similarity` can use
            a fast dot product.
        batch_size: Internal batch size for :meth:`embed`.  Larger
            batches use more memory but improve throughput on GPU.
    """

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        *,
        normalize_embeddings: bool = True,
        batch_size: int = 32,
    ) -> None:
        if SentenceTransformer is None:
            raise ImportError(_INSTALL_HINT)
        self._model_name = model_name
        self._normalize = normalize_embeddings
        self._batch_size = batch_size
        self._model: Any = SentenceTransformer(model_name)

    @property
    def model_name(self) -> str:
        """The Hugging Face model id loaded by this backend."""
        return self._model_name

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per entry in *texts*.

        Empty input returns an empty list (zero-batch shortcut).
        """
        if not texts:
            return []
        encoded = self._model.encode(
            texts,
            batch_size=self._batch_size,
            convert_to_numpy=True,
            normalize_embeddings=self._normalize,
            show_progress_bar=False,
        )
        # sentence-transformers returns a numpy.ndarray; cast row-wise to
        # plain python lists so this module never leaks numpy into the
        # protocol surface.
        return [[float(x) for x in row] for row in encoded]

    def similarity(
        self,
        query_vec: list[float],
        corpus_vecs: list[list[float]],
    ) -> list[float]:
        """Cosine similarity (or dot product when both sides are unit-norm)."""
        if self._normalize:
            return [_dot(query_vec, v) for v in corpus_vecs]
        q_norm = _norm(query_vec)
        if q_norm == 0.0:
            return [0.0] * len(corpus_vecs)
        out: list[float] = []
        for v in corpus_vecs:
            v_norm = _norm(v)
            if v_norm == 0.0:
                out.append(0.0)
                continue
            out.append(_dot(query_vec, v) / (q_norm * v_norm))
        return out


class HashingEmbeddingBackend:
    """Stdlib-only deterministic :class:`~contextweaver.protocols.EmbeddingBackend`.

    Uses the classic *hashing trick*: each token is hashed into a fixed
    number of dimensions, contributing a signed weight derived from a
    second hash bit.  Vectors are L2-normalised so :meth:`similarity` is
    a plain dot product.

    The backend ships zero ML model — it captures lexical co-occurrence
    only.  It is **not** a substitute for a real embedding model
    (sentence-transformers, OpenAI, etc.); its purpose is to provide:

    * a deterministic, reproducible reference implementation of the
      :class:`~contextweaver.protocols.EmbeddingBackend` protocol that
      requires no third-party dependencies,
    * a stable baseline row in the benchmark scorecard so the
      embedding-retrieval path is exercised in CI without pulling
      ``torch`` (#266), and
    * a sane default for users who want to experiment with the
      embedding code path before installing the heavier
      ``[embeddings]`` extra.

    Args:
        n_features: Number of output dimensions.  Powers of two work
            best with the bit-shifting hash mask; 1024 is the default
            and is enough to keep namespace collisions low on catalogs
            up to a few thousand items.
        ngram_range: ``(min_n, max_n)`` character n-grams in addition
            to whitespace tokens.  ``(0, 0)`` disables n-grams (token
            mode only); the default ``(3, 5)`` captures sub-word signal
            similar to what real embedding models recover via WordPiece.
        seed: Salt mixed into the hash so two independent
            :class:`HashingEmbeddingBackend` instances can produce
            independent projections.  ``0`` is the deterministic default.

    Determinism: identical ``(n_features, ngram_range, seed)`` → identical
    output vectors for identical input across Python interpreter runs
    (the backend uses ``hashlib.blake2b`` rather than the salted built-in
    ``hash()`` to avoid ``PYTHONHASHSEED`` interference).
    """

    def __init__(
        self,
        *,
        n_features: int = 1024,
        ngram_range: tuple[int, int] = (3, 5),
        seed: int = 0,
    ) -> None:
        if n_features <= 0:
            from contextweaver.exceptions import ConfigError

            raise ConfigError(f"n_features must be > 0, got {n_features}")
        if ngram_range[0] < 0 or ngram_range[1] < ngram_range[0]:
            from contextweaver.exceptions import ConfigError

            raise ConfigError(f"ngram_range must satisfy 0 <= min <= max, got {ngram_range!r}")
        self._n_features = n_features
        self._ngram_min, self._ngram_max = ngram_range
        self._seed_bytes = seed.to_bytes(8, "little", signed=False)

    @property
    def n_features(self) -> int:
        """Output vector dimensionality."""
        return self._n_features

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one L2-normalised vector per text."""
        return [self._embed_one(t) for t in texts]

    def similarity(
        self,
        query_vec: list[float],
        corpus_vecs: list[list[float]],
    ) -> list[float]:
        """Cosine similarity (== dot product since vectors are unit-norm)."""
        return [_dot(query_vec, v) for v in corpus_vecs]

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self._n_features
        for token in self._features(text):
            idx, sign = self._hash(token)
            vec[idx] += sign
        # L2-normalise so downstream similarity is a clean dot product.
        norm = math.sqrt(sum(x * x for x in vec))
        if norm == 0.0:
            return vec
        return [x / norm for x in vec]

    def _features(self, text: str) -> list[str]:
        """Yield tokens + character n-grams used as hash inputs."""
        toks = tokenize(text)
        feats: list[str] = list(toks)
        if self._ngram_min > 0:
            lowered = text.lower()
            for n in range(self._ngram_min, self._ngram_max + 1):
                for i in range(0, len(lowered) - n + 1):
                    feats.append(lowered[i : i + n])
        return feats

    def _hash(self, token: str) -> tuple[int, float]:
        """Map *token* to ``(feature_index, signed_weight)``.

        Uses ``blake2b`` so the result is independent of
        ``PYTHONHASHSEED``.  The first 8 bytes select the feature
        index; bit 0 of the next byte selects the sign.
        """
        import hashlib

        h = hashlib.blake2b(token.encode("utf-8"), digest_size=9, salt=self._seed_bytes)
        digest = h.digest()
        idx = int.from_bytes(digest[:8], "little", signed=False) % self._n_features
        sign = 1.0 if (digest[8] & 1) == 0 else -1.0
        return idx, sign


class HybridEmbeddingRetriever:
    """:class:`~contextweaver.protocols.Retriever` combining embeddings + TF-IDF.

    Issue #8 acceptance criterion: "when an embedding backend is provided,
    Router uses it for initial candidate scoring, with TF-IDF as a
    secondary signal."  This retriever realises that contract with a
    weighted sum so the embedding backend is the *primary* signal while
    the lexical TF-IDF score keeps exact-id / exact-tag hits from being
    drowned out.

    Args:
        backend: Any :class:`EmbeddingBackend` implementation.
        embedding_weight: Weight on the embedding similarity component
            (default 0.7).  Must be in ``[0.0, 1.0]``.  The TF-IDF weight
            is ``1.0 - embedding_weight``.

    Determinism: identical (corpus, query) → identical scores **for a
    given backend instance**.  Re-instantiating a stateful backend with
    a different model version or device will produce different scores —
    this is a known limitation called out in the module docstring.
    """

    def __init__(
        self,
        backend: EmbeddingBackend,
        *,
        embedding_weight: float = 0.7,
    ) -> None:
        if not 0.0 <= embedding_weight <= 1.0:
            from contextweaver.exceptions import ConfigError

            raise ConfigError(f"embedding_weight must be in [0.0, 1.0], got {embedding_weight}")
        self._backend = backend
        self._embedding_weight = embedding_weight
        self._tfidf_weight = 1.0 - embedding_weight
        self._corpus_size = 0
        self._corpus_vecs: list[list[float]] = []
        self._tfidf = TfIdfScorer()
        # LRU-1 cache: avoid re-embedding the same query when score_one is
        # called repeatedly with the same query (e.g., _result_similarity_map
        # in Router).  Keyed by query string; invalidated on new query.
        self._cached_query: str | None = None
        self._cached_emb_scores: list[float] = []

    @property
    def backend(self) -> EmbeddingBackend:
        """The wrapped :class:`EmbeddingBackend`."""
        return self._backend

    def fit(self, corpus: list[str]) -> None:
        """Embed *corpus* once and fit the secondary TF-IDF scorer.

        Re-calling :meth:`fit` recomputes both sides; the previous corpus
        is discarded.
        """
        self._corpus_size = len(corpus)
        self._corpus_vecs = self._backend.embed(corpus)
        self._tfidf.fit(corpus)
        # Invalidate embedding score cache on corpus change.
        self._cached_query = None
        self._cached_emb_scores = []

    def search(self, query: str, top_k: int) -> list[tuple[int, float]]:
        """Return up to *top_k* ``(index, hybrid_score)`` pairs sorted by score desc."""
        if self._corpus_size == 0:
            return []
        emb_scores = self._embedding_scores(query)
        scored: list[tuple[int, float]] = []
        for i in range(self._corpus_size):
            tfidf = self._tfidf.score(query, i)
            score = self._embedding_weight * emb_scores[i] + self._tfidf_weight * tfidf
            scored.append((i, score))
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored[: max(0, top_k)]

    def score_one(self, query: str, index: int) -> float:
        """Return the hybrid score for the document at *index*."""
        if not 0 <= index < self._corpus_size:
            return 0.0
        emb = self._embedding_scores(query)[index]
        tfidf = self._tfidf.score(query, index)
        return self._embedding_weight * emb + self._tfidf_weight * tfidf

    def _embedding_scores(self, query: str) -> list[float]:
        """Per-corpus-document similarity to *query* using :attr:`backend`.

        Uses an LRU-1 cache so repeated calls with the same query (the
        common case in :meth:`score_one` loops) pay only one embedding
        pass.
        """
        if query == self._cached_query:
            return self._cached_emb_scores
        if not self._corpus_vecs:
            return []
        q_vec = self._backend.embed([query])
        if not q_vec:
            scores = [0.0] * self._corpus_size
        else:
            scores = self._backend.similarity(q_vec[0], self._corpus_vecs)
        self._cached_query = query
        self._cached_emb_scores = scores
        return scores


def _dot(a: list[float], b: list[float]) -> float:
    """Dot product of two equal-length lists.  Returns 0.0 on length mismatch."""
    if len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=False))


def _norm(v: list[float]) -> float:
    """L2 norm."""
    return math.sqrt(sum(x * x for x in v))


__all__ = [
    "HashingEmbeddingBackend",
    "HybridEmbeddingRetriever",
    "SentenceTransformerBackend",
]

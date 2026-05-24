"""Stdlib-only deterministic :class:`EmbeddingBackend` (issue #266).

Lives in its own module to keep
:mod:`contextweaver.extras.embeddings` under the project's 300-line
module guideline.  Re-exported from
:mod:`contextweaver.extras.embeddings` for backwards compatibility
with callers that imported ``HashingEmbeddingBackend`` from the
original location.
"""

from __future__ import annotations

import hashlib
import math

from contextweaver._utils import tokenize
from contextweaver.exceptions import ConfigError

# ``blake2b`` salt is 8 bytes wide, so the seed lives in ``[0, 2**64)``.
# Anything outside the range would raise ``OverflowError`` deep inside
# ``int.to_bytes`` — we surface that as a :class:`ConfigError` per the
# project's "no bare ``ValueError`` / ``OverflowError``" rule.
_MAX_SEED = (1 << 64) - 1


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
            independent projections.  Must fit in 64 unsigned bits
            (``0 <= seed < 2**64``); ``0`` is the deterministic default.

    Raises:
        ConfigError: If *n_features* is not positive, *ngram_range* is
            malformed, or *seed* is outside ``[0, 2**64)``.

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
            raise ConfigError(f"n_features must be > 0, got {n_features}")
        if ngram_range[0] < 0 or ngram_range[1] < ngram_range[0]:
            raise ConfigError(f"ngram_range must satisfy 0 <= min <= max, got {ngram_range!r}")
        if not 0 <= seed <= _MAX_SEED:
            raise ConfigError(f"seed must satisfy 0 <= seed < 2**64, got {seed}")
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
        h = hashlib.blake2b(token.encode("utf-8"), digest_size=9, salt=self._seed_bytes)
        digest = h.digest()
        idx = int.from_bytes(digest[:8], "little", signed=False) % self._n_features
        sign = 1.0 if (digest[8] & 1) == 0 else -1.0
        return idx, sign


def _dot(a: list[float], b: list[float]) -> float:
    """Dot product of two equal-length lists.  Returns 0.0 on length mismatch."""
    if len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=False))


__all__ = ["HashingEmbeddingBackend"]

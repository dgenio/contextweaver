"""Text-similarity utilities used across the contextweaver library.

This module is the single source of truth for tokenisation and similarity
computations.  All other modules that need text similarity should import from
here rather than implementing their own variants.

Public API:
    - :data:`STOPWORDS` — frozen set of ~100 common English stop-words
    - :func:`tokenize` — normalise + tokenise a string to a ``set[str]``
    - :func:`jaccard` — Jaccard similarity between two token sets
    - :class:`TfIdfScorer` — lightweight, deterministic TF-IDF scorer
"""

from __future__ import annotations

import math
import re
from collections import Counter

# ---------------------------------------------------------------------------
# Stop-words
# ---------------------------------------------------------------------------

STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "about",
        "above",
        "after",
        "again",
        "against",
        "all",
        "am",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "but",
        "by",
        "can",
        "cannot",
        "could",
        "did",
        "do",
        "does",
        "doing",
        "down",
        "during",
        "each",
        "few",
        "for",
        "from",
        "further",
        "get",
        "had",
        "has",
        "have",
        "having",
        "he",
        "her",
        "here",
        "hers",
        "herself",
        "him",
        "himself",
        "his",
        "how",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "itself",
        "just",
        "me",
        "more",
        "most",
        "my",
        "myself",
        "no",
        "nor",
        "not",
        "now",
        "of",
        "off",
        "on",
        "once",
        "only",
        "or",
        "other",
        "our",
        "ours",
        "ourselves",
        "out",
        "over",
        "own",
        "same",
        "she",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "theirs",
        "them",
        "themselves",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "until",
        "up",
        "us",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "yours",
        "yourself",
        "yourselves",
    }
)

# ---------------------------------------------------------------------------
# Tokenisation
# ---------------------------------------------------------------------------

_SPLIT_RE = re.compile(r"\W+")


def tokenize(text: str) -> set[str]:
    """Split on ``\\W+``, lowercase, filter len >= 2, remove stopwords, deduplicate."""
    tokens = _SPLIT_RE.split(text.lower())
    return {t for t in tokens if len(t) >= 2 and t not in STOPWORDS}


# ---------------------------------------------------------------------------
# Jaccard similarity
# ---------------------------------------------------------------------------


def jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity: |a ∩ b| / |a ∪ b|. Returns 0.0 if union is empty."""
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


# ---------------------------------------------------------------------------
# TF-IDF scorer
# ---------------------------------------------------------------------------


class TfIdfScorer:
    """Pure-Python TF-IDF scorer. No external deps. Deterministic."""

    def __init__(self, stopwords: frozenset[str] | None = None) -> None:
        self._stopwords = stopwords if stopwords is not None else STOPWORDS
        self._documents: list[list[str]] = []
        self._idf: dict[str, float] = {}

    def fit(self, documents: list[str]) -> TfIdfScorer:
        """Fit on a corpus. Returns self for chaining."""
        tokenized = [sorted(tokenize(doc)) for doc in documents]
        self._documents = tokenized
        n = len(documents)
        if n == 0:
            self._idf = {}
            return self
        df: Counter[str] = Counter()
        for tokens in tokenized:
            for tok in set(tokens):
                df[tok] += 1
        self._idf = {
            term: math.log((1 + n) / (1 + freq)) + 1.0 for term, freq in sorted(df.items())
        }
        return self

    def score(self, query: str, doc_index: int) -> float:
        """Compute the TF-IDF score of *query* against a single document."""
        if doc_index < 0 or doc_index >= len(self._documents):
            raise IndexError(f"doc_index {doc_index} out of range ({len(self._documents)} docs)")
        q_tokens = tokenize(query)
        doc_tokens = self._documents[doc_index]
        if not q_tokens or not doc_tokens:
            return 0.0
        tf: Counter[str] = Counter(doc_tokens)
        total = len(doc_tokens)
        result = 0.0
        for term in sorted(q_tokens):
            idf = self._idf.get(term, math.log((1 + len(self._documents)) / 1) + 1.0)
            result += (tf[term] / total) * idf
        return result

    def score_all(self, query: str) -> list[tuple[int, float]]:
        """Returns (doc_index, score) sorted by score desc."""
        pairs = [(i, self.score(query, i)) for i in range(len(self._documents))]
        pairs.sort(key=lambda x: (-x[1], x[0]))
        return pairs

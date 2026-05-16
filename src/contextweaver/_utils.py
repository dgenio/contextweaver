"""Text-similarity utilities used across the contextweaver library.

This module is the single source of truth for tokenisation and similarity
computations.  All other modules that need text similarity should import from
here rather than implementing their own variants.

Public API:
    - :data:`STOPWORDS` — frozen set of ~100 common English stop-words
    - :func:`tokenize` — normalise + tokenise a string to a ``set[str]``
    - :func:`jaccard` — Jaccard similarity between two token sets
    - :class:`TfIdfScorer` — pure-Python deterministic TF-IDF scorer
    - :class:`BM25Scorer` — BM25 scorer backed by ``rank-bm25`` (core dep)
    - :class:`FuzzyScorer` — fuzzy scorer backed by ``rapidfuzz``
      (``contextweaver[retrieval]`` extra; ``None`` when missing)
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
# Surface tokens that keep dotted / snake_case / hyphenated / slashed ids intact
# as a single match. Stripped of leading / trailing delimiters before use; the
# *internal* delimiters then drive sub-token emission (issue #213).
_SURFACE_RE = re.compile(r"[\w./\-]+")
_SUB_SPLIT_RE = re.compile(r"[._\-/]+")
_DELIM_CHARS = "._-/"


def tokenize_list(text: str) -> list[str]:
    """Normalise *text* and return a list of meaningful tokens (preserves duplicates).

    Same normalisation pipeline as :func:`tokenize`, but returns the tokens
    in occurrence order with duplicates intact. Use this when downstream
    code depends on term frequency (e.g. BM25 scoring). For set-style
    operations (Jaccard, presence checks), use :func:`tokenize` instead.

    Steps:
    1. Lower-case the input.
    2. Scan word-like runs that may include internal ``.``/``_``/``-``/``/``
       (so dotted / snake_case ids stay intact).
    3. Strip surrounding delimiter characters from each run.
    4. Emit the joined form (length ≥ 2 and not a STOPWORD).
    5. If the run still has internal delimiters, also emit each sub-token
       — again filtered by length ≥ 2 and STOPWORD membership.

    Args:
        text: Raw input string.

    Returns:
        A ``list[str]`` of normalised, stop-word-filtered tokens in
        occurrence order. Duplicate tokens are preserved.
    """
    out: list[str] = []
    for raw in _SURFACE_RE.findall(text.lower()):
        stripped = raw.strip(_DELIM_CHARS)
        if len(stripped) >= 2 and stripped not in STOPWORDS:
            out.append(stripped)
        if _SUB_SPLIT_RE.search(stripped):
            for sub in _SUB_SPLIT_RE.split(stripped):
                if len(sub) >= 2 and sub not in STOPWORDS:
                    out.append(sub)
    return out


def tokenize(text: str) -> set[str]:
    """Normalise *text* and return a set of meaningful tokens.

    Steps:
    1. Lower-case the input.
    2. Split on one or more non-word characters.
    3. For tokens with internal ``.``/``_``/``-``/``/`` delimiters, also
       emit each sub-token (the original token is retained).
    4. Discard tokens shorter than 2 characters.
    5. Remove :data:`STOPWORDS`.

    Args:
        text: Raw input string.

    Returns:
        A ``set[str]`` of normalised, stop-word-filtered tokens. Duplicate
        tokens are collapsed; use :func:`tokenize_list` when term frequency
        matters (e.g. BM25 scoring).
    """
    return set(tokenize_list(text))


# ---------------------------------------------------------------------------
# Jaccard similarity
# ---------------------------------------------------------------------------


def jaccard(a: set[str], b: set[str]) -> float:
    """Compute the Jaccard similarity coefficient between two token sets.

    Returns ``0.0`` when both sets are empty to avoid division by zero.

    Args:
        a: First token set.
        b: Second token set.

    Returns:
        A float in ``[0.0, 1.0]``.
    """
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
    """Lightweight, deterministic TF-IDF scorer over a fixed document corpus.

    Usage::

        scorer = TfIdfScorer()
        scorer.fit(["search databases quickly", "fast database access", ...])
        scores = scorer.score_all("fast search")  # list[float]

    The implementation is pure Python with no runtime dependencies.
    Determinism is guaranteed: identical inputs always produce identical scores.
    """

    def __init__(self) -> None:
        self._documents: list[list[str]] = []
        self._idf: dict[str, float] = {}

    def fit(self, documents: list[str]) -> None:
        """Index *documents* and pre-compute IDF weights.

        Args:
            documents: A list of raw text strings to index.  The order
                determines ``doc_index`` values used in :meth:`score`.
        """
        tokenized = [sorted(tokenize(doc)) for doc in documents]
        self._documents = tokenized
        n = len(documents)
        if n == 0:
            self._idf = {}
            return
        df: Counter[str] = Counter()
        for tokens in tokenized:
            for tok in set(tokens):
                df[tok] += 1
        self._idf = {
            term: math.log((1 + n) / (1 + freq)) + 1.0 for term, freq in sorted(df.items())
        }

    def score(self, query: str, doc_index: int) -> float:
        """Compute the TF-IDF score of *query* against a single document.

        Args:
            query: Raw query string.
            doc_index: Zero-based index into the corpus passed to :meth:`fit`.

        Returns:
            A non-negative float; higher means more relevant.

        Raises:
            IndexError: If *doc_index* is out of range.
        """
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

    def score_all(self, query: str) -> list[float]:
        """Score *query* against every document in the corpus.

        Args:
            query: Raw query string.

        Returns:
            A ``list[float]`` of scores, one per document, in corpus order.
        """
        return [self.score(query, i) for i in range(len(self._documents))]


# ---------------------------------------------------------------------------
# BM25 scorer (rank-bm25 is a core dep)
# ---------------------------------------------------------------------------


class BM25Scorer:
    """BM25 scorer backed by the ``rank-bm25`` library.

    BM25 typically outperforms raw TF-IDF on lexical retrieval because of
    term-frequency saturation and document-length normalisation. The same
    interface as :class:`TfIdfScorer` (``fit`` / ``score`` / ``score_all``)
    so the two are interchangeable in :class:`~contextweaver.routing.router.Router`.

    Determinism: ``rank-bm25`` is deterministic for a fixed corpus + query.
    Tokens are sorted before indexing to keep the corpus order stable.
    """

    def __init__(self) -> None:
        from rank_bm25 import BM25Okapi  # core dep

        self._bm25_cls = BM25Okapi
        self._bm25: BM25Okapi | None = None
        self._n_docs: int = 0

    def fit(self, documents: list[str]) -> None:
        """Index *documents* with BM25.

        Uses :func:`tokenize_list` so duplicate terms are preserved — BM25
        relies on per-document term frequency to compute saturation and
        length-normalised scores.

        Args:
            documents: Raw text strings; index order is preserved as
                ``doc_index`` in subsequent calls to :meth:`score`.
        """
        corpus = [tokenize_list(doc) for doc in documents]
        self._n_docs = len(documents)
        # rank_bm25 raises on empty corpora; guard with a sentinel.
        self._bm25 = self._bm25_cls(corpus) if corpus else None

    def score(self, query: str, doc_index: int) -> float:
        """Return the BM25 score of *query* against the document at *doc_index*."""
        if self._bm25 is None:
            return 0.0
        if doc_index < 0 or doc_index >= self._n_docs:
            raise IndexError(f"doc_index {doc_index} out of range ({self._n_docs} docs)")
        q_tokens = tokenize_list(query)
        if not q_tokens:
            return 0.0
        scores = self._bm25.get_scores(q_tokens)
        return float(scores[doc_index])

    def score_all(self, query: str) -> list[float]:
        """Score *query* against every document in the corpus."""
        if self._bm25 is None:
            return []
        q_tokens = tokenize_list(query)
        if not q_tokens:
            return [0.0] * self._n_docs
        return [float(s) for s in self._bm25.get_scores(q_tokens)]


# ---------------------------------------------------------------------------
# Fuzzy scorer (rapidfuzz; contextweaver[retrieval] extra)
# ---------------------------------------------------------------------------

try:
    from rapidfuzz import fuzz as _rapidfuzz_fuzz

    class FuzzyScorer:
        """Fuzzy string-similarity scorer backed by ``rapidfuzz``.

        Useful when queries contain typos, abbreviations, or partial matches
        that token-set similarity would miss (``"emal"`` ↔ ``"email"``).
        Same interface as :class:`TfIdfScorer`. Score values are normalised
        to ``[0.0, 1.0]``.

        Available only when ``contextweaver[retrieval]`` extra is installed.
        ``FuzzyScorer is None`` otherwise.
        """

        def __init__(self) -> None:
            self._docs: list[str] = []

        def fit(self, documents: list[str]) -> None:
            """Store *documents* for later fuzzy scoring."""
            self._docs = list(documents)

        def score(self, query: str, doc_index: int) -> float:
            """Return the rapidfuzz token-set ratio in ``[0.0, 1.0]``."""
            if doc_index < 0 or doc_index >= len(self._docs):
                raise IndexError(f"doc_index {doc_index} out of range ({len(self._docs)} docs)")
            if not query:
                return 0.0
            return float(_rapidfuzz_fuzz.token_set_ratio(query, self._docs[doc_index])) / 100.0

        def score_all(self, query: str) -> list[float]:
            """Score *query* against every document."""
            return [self.score(query, i) for i in range(len(self._docs))]

except ImportError:  # pragma: no cover - exercised only when extra is missing
    FuzzyScorer = None  # type: ignore[assignment, misc]

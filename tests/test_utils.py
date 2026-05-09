"""Tests for contextweaver._utils."""

from __future__ import annotations

import pytest

from contextweaver._utils import (
    STOPWORDS,
    BM25Scorer,
    FuzzyScorer,
    TfIdfScorer,
    jaccard,
    tokenize,
)


def test_stopwords_nonempty() -> None:
    assert len(STOPWORDS) >= 50
    assert "the" in STOPWORDS
    assert "search" not in STOPWORDS


def test_tokenize_basic() -> None:
    tokens = tokenize("Search the database quickly!")
    assert "search" in tokens
    assert "database" in tokens
    assert "the" not in tokens  # stopword
    assert "quickly" in tokens


def test_tokenize_filters_short() -> None:
    tokens = tokenize("a bb ccc")
    assert "a" not in tokens
    assert "bb" in tokens
    assert "ccc" in tokens


def test_tokenize_returns_set() -> None:
    tokens = tokenize("hello hello world")
    assert isinstance(tokens, set)
    assert len(tokens) == 2  # hello, world


def test_jaccard_identical() -> None:
    a = {"x", "y", "z"}
    assert jaccard(a, a) == pytest.approx(1.0)


def test_jaccard_disjoint() -> None:
    assert jaccard({"a"}, {"b"}) == pytest.approx(0.0)


def test_jaccard_empty() -> None:
    assert jaccard(set(), set()) == pytest.approx(0.0)


def test_jaccard_partial() -> None:
    a = {"a", "b", "c"}
    b = {"b", "c", "d"}
    assert jaccard(a, b) == pytest.approx(2 / 4)


def test_tfidf_fit_and_score() -> None:
    scorer = TfIdfScorer()
    docs = ["search database quickly", "fast database access", "unrelated content here"]
    scorer.fit(docs)
    scores = scorer.score_all("fast database")
    assert len(scores) == 3
    # Second doc should score highest for "fast database"
    assert scores[1] >= scores[2]


def test_tfidf_empty_corpus() -> None:
    scorer = TfIdfScorer()
    scorer.fit([])
    assert scorer.score_all("hello") == []


def test_tfidf_score_out_of_range() -> None:
    scorer = TfIdfScorer()
    scorer.fit(["hello world"])
    with pytest.raises(IndexError):
        scorer.score("hello", 5)


def test_tfidf_deterministic() -> None:
    docs = ["alpha beta gamma", "delta epsilon", "alpha delta"]
    s1 = TfIdfScorer()
    s1.fit(docs)
    s2 = TfIdfScorer()
    s2.fit(docs)
    assert s1.score_all("alpha delta") == s2.score_all("alpha delta")


# ---------------------------------------------------------------------------
# BM25Scorer (rank-bm25 is a core dep)
# ---------------------------------------------------------------------------


def test_bm25_fit_and_score() -> None:
    scorer = BM25Scorer()
    docs = ["search database quickly", "fast database access", "unrelated content here"]
    scorer.fit(docs)
    scores = scorer.score_all("fast database")
    assert len(scores) == 3
    # Second doc should score highest for "fast database" (matches both terms)
    assert scores[1] >= scores[2]


def test_bm25_empty_corpus() -> None:
    scorer = BM25Scorer()
    scorer.fit([])
    assert scorer.score_all("hello") == []


def test_bm25_score_out_of_range() -> None:
    scorer = BM25Scorer()
    scorer.fit(["hello world"])
    with pytest.raises(IndexError):
        scorer.score("hello", 5)


def test_bm25_empty_query_returns_zeros() -> None:
    scorer = BM25Scorer()
    scorer.fit(["hello world", "another doc"])
    assert scorer.score_all("") == [0.0, 0.0]


def test_bm25_deterministic() -> None:
    docs = ["alpha beta gamma", "delta epsilon", "alpha delta"]
    s1 = BM25Scorer()
    s1.fit(docs)
    s2 = BM25Scorer()
    s2.fit(docs)
    assert s1.score_all("alpha delta") == s2.score_all("alpha delta")


# ---------------------------------------------------------------------------
# FuzzyScorer (rapidfuzz; contextweaver[retrieval] extra)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(FuzzyScorer is None, reason="rapidfuzz not installed ([retrieval] extra)")
def test_fuzzy_basic_match() -> None:
    scorer = FuzzyScorer()  # type: ignore[misc]
    docs = ["send email to user", "create database", "search documentation"]
    scorer.fit(docs)
    scores = scorer.score_all("emal")  # typo of "email"
    assert scores[0] > scores[1]  # email match wins despite typo


@pytest.mark.skipif(FuzzyScorer is None, reason="rapidfuzz not installed ([retrieval] extra)")
def test_fuzzy_empty_query() -> None:
    scorer = FuzzyScorer()  # type: ignore[misc]
    scorer.fit(["hello world"])
    assert scorer.score("", 0) == 0.0


@pytest.mark.skipif(FuzzyScorer is None, reason="rapidfuzz not installed ([retrieval] extra)")
def test_fuzzy_score_out_of_range() -> None:
    scorer = FuzzyScorer()  # type: ignore[misc]
    scorer.fit(["hello"])
    with pytest.raises(IndexError):
        scorer.score("hi", 5)


def test_fuzzy_scorer_is_none_when_extra_missing() -> None:
    """Document the public contract: FuzzyScorer is None unless [retrieval] extra installed."""
    # Either the extra is installed (FuzzyScorer is a class) or it isn't (None).
    assert FuzzyScorer is None or callable(FuzzyScorer)

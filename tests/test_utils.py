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
    tokenize_list,
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


# ---------------------------------------------------------------------------
# Namespace-aware sub-token splitting (issue #213)
# ---------------------------------------------------------------------------


def test_tokenize_splits_dotted_id() -> None:
    """Dotted tool ids contribute both the joined form and component parts."""
    tokens = tokenize("crm.deals.search")
    assert "crm.deals.search" in tokens  # original retained
    assert "crm" in tokens
    assert "deals" in tokens
    assert "search" in tokens


def test_tokenize_splits_underscored_id() -> None:
    """Snake_case ids split on underscores (which \\W+ leaves alone)."""
    tokens = tokenize("billing_invoices_search")
    assert "billing_invoices_search" in tokens
    assert "billing" in tokens
    assert "invoices" in tokens
    assert "search" in tokens


def test_tokenize_splits_hyphenated_id() -> None:
    tokens = tokenize("tool-execute-call")
    assert "tool-execute-call" in tokens
    assert "tool" in tokens
    assert "execute" in tokens
    assert "call" in tokens


def test_tokenize_splits_slash_path() -> None:
    tokens = tokenize("admin/audit/export")
    assert "admin" in tokens
    assert "audit" in tokens
    assert "export" in tokens


def test_tokenize_stopwords_filtered_from_subtokens() -> None:
    """STOPWORDS apply to sub-tokens, not just surface tokens."""
    tokens = tokenize("the_search")
    # ``the`` is a stopword and must be filtered out of the sub-token output.
    assert "the" not in tokens
    assert "search" in tokens
    # the joined form survives because it's not itself a stopword.
    assert "the_search" in tokens


def test_tokenize_short_subtokens_filtered() -> None:
    """Sub-tokens shorter than 2 chars are dropped, same as surface tokens."""
    tokens = tokenize("a.search")
    assert "a" not in tokens  # single char
    assert "search" in tokens


def test_tokenize_no_delimiter_unchanged() -> None:
    """Tokens without internal delimiters behave exactly as before."""
    tokens = tokenize("search database quickly")
    assert tokens == {"search", "database", "quickly"}


def test_tokenize_deterministic_100x() -> None:
    """The augmented tokenizer is deterministic across repeated calls."""
    expected = tokenize("crm.deals.search billing_invoices")
    for _ in range(100):
        assert tokenize("crm.deals.search billing_invoices") == expected


def test_tokenize_list_preserves_order_and_duplicates() -> None:
    """tokenize_list emits the surface token, then its sub-tokens, in order."""
    result = tokenize_list("crm.deals search crm.deals")
    # crm.deals -> joined, crm, deals (first occurrence), then "search",
    # then crm.deals again -> joined, crm, deals.
    assert result == [
        "crm.deals",
        "crm",
        "deals",
        "search",
        "crm.deals",
        "crm",
        "deals",
    ]


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


def test_bm25_preserves_term_frequency() -> None:
    """BM25 must count term frequency.

    Regression for PR #188 review — `BM25Scorer.fit()` previously fed
    `sorted(tokenize(doc))` to `BM25Okapi`, but `tokenize()` returned a
    ``set[str]`` which discarded duplicates. With TF lost the scorer
    degraded to binary matching: a doc mentioning the query term once
    scored the same as one mentioning it many times.

    With `tokenize_list()` in place, a doc that repeats the query term
    multiple times must score strictly higher than one that mentions it
    only once.
    """
    scorer = BM25Scorer()
    # Doc 0 mentions "database" three times; doc 1 mentions it once.
    # Three distractor docs that don't mention the query term keep the
    # `database` IDF positive (otherwise a query term present in every
    # doc would have a non-positive IDF and TF would lose its boost).
    scorer.fit(
        [
            "database database database lookup tool",
            "database lookup tool",
            "unrelated alpha beta gamma",
            "another bravo charlie delta",
            "echo foxtrot golf hotel",
        ]
    )
    scores = scorer.score_all("database")
    assert scores[0] > scores[1], (
        f"BM25 must reward higher term frequency; "
        f"got scores[0]={scores[0]!r} <= scores[1]={scores[1]!r} — "
        f"indicates tokenize_list() is not being used in BM25Scorer.fit()."
    )


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

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
# Namespace-aware compound tokenisation (issue #213)
# ---------------------------------------------------------------------------


def test_tokenize_dotted_compound_emits_both_forms() -> None:
    """Dotted tool ids emit the compound and the per-segment sub-tokens."""
    tokens = tokenize("crm.deals.search")
    assert tokens == {"crm.deals.search", "crm", "deals", "search"}


def test_tokenize_underscore_compound_stays_intact() -> None:
    """Underscored compounds are emitted as a single token by design (#213).

    Deviation from the original #213 AC: empirical measurement on the v0.3.0
    benchmark showed that splitting on ``_`` inflates cross-talk between
    unrelated tools because the synthetic catalog generator
    (``benchmarks/benchmark.py:_make_catalog``) uses underscored variant
    suffixes like ``invoices_search_v2``. Splitting those reintroduces gold
    query segments into the noise vocabulary and regresses recall@5 by
    several pp on the 50-query gold set at catalog_size ≥ 500. See the
    namespace separator rationale in ``_OUTER_SPLIT_RE``.
    """
    tokens = tokenize("billing_invoices_search")
    assert tokens == {"billing_invoices_search"}


def test_tokenize_hyphen_compound_emits_both_forms() -> None:
    """Hyphenated names emit compound + per-segment sub-tokens."""
    tokens = tokenize("infra-deployments-create")
    assert tokens == {"infra-deployments-create", "infra", "deployments", "create"}


def test_tokenize_slash_compound_emits_both_forms() -> None:
    """Slash-separated names (e.g. tool paths) emit compound + segments."""
    tokens = tokenize("search/web/scrape")
    assert tokens == {"search/web/scrape", "search", "web", "scrape"}


def test_tokenize_colon_does_not_form_compound() -> None:
    """``:`` is a hard separator: produces only the segments, no compound."""
    tokens = tokenize("admin:users:create")
    assert tokens == {"admin", "users", "create"}


def test_tokenize_mixed_namespace_separators_in_one_compound() -> None:
    """Mixed ``.`` / ``-`` inside one compound emit compound + namespace segments.

    Underscore is preserved (not a namespace separator — see
    ``test_tokenize_underscore_compound_stays_intact``), so an underscored
    sub-segment such as ``deals_pipeline`` is kept whole.
    """
    tokens = tokenize("crm.deals_pipeline-stage")
    assert "crm.deals_pipeline-stage" in tokens
    assert {"crm", "deals_pipeline", "stage"}.issubset(tokens)


def test_tokenize_non_compound_unchanged() -> None:
    """Single-word tokens emit exactly themselves — no spurious compound form."""
    tokens = tokenize("search")
    assert tokens == {"search"}


def test_tokenize_leading_trailing_separators_stripped() -> None:
    """``.search.`` behaves like ``search`` — surrounding seps are stripped."""
    assert tokenize(".search.") == {"search"}
    assert tokenize("-deploy/") == {"deploy"}


def test_tokenize_empty_and_separators_only() -> None:
    """Empty input or separator-only input produces an empty set."""
    assert tokenize("") == set()
    assert tokenize(".") == set()
    assert tokenize("./-") == set()
    assert tokenize("   ") == set()


def test_tokenize_repeated_inner_separators() -> None:
    """``crm..deals`` (repeated dots) still emits compound + 2 segments."""
    tokens = tokenize("crm..deals")
    assert tokens == {"crm..deals", "crm", "deals"}


def test_tokenize_stopword_compound_segments() -> None:
    """Stopword segments are dropped from the segment emission."""
    # "the" is a stopword; segment is dropped but the compound survives
    # because the compound itself isn't a stopword.
    tokens = tokenize("deploy-the-app")
    assert "deploy-the-app" in tokens
    assert "deploy" in tokens
    assert "app" in tokens
    assert "the" not in tokens


def test_tokenize_natural_text_unaffected() -> None:
    """Existing natural-text tokenisation behaviour is unchanged."""
    # Same expectations as the pre-#213 baseline.
    assert tokenize("Search the database quickly!") == {"search", "database", "quickly"}
    assert tokenize("hello hello world") == {"hello", "world"}


def test_tokenize_deterministic_n100() -> None:
    """Set equality must hold across N=100 calls on the same input."""
    text = "search billing.invoices.search by customer_email"
    first = tokenize(text)
    for _ in range(100):
        assert tokenize(text) == first
    # tokenize_list must also be order-stable
    first_list = tokenize_list(text)
    for _ in range(100):
        assert tokenize_list(text) == first_list


def test_tokenize_list_preserves_compound_then_segments_order() -> None:
    """tokenize_list emits compound first, then segments, in occurrence order."""
    tokens = tokenize_list("crm.deals.search")
    assert tokens[0] == "crm.deals.search"
    assert tokens[1:] == ["crm", "deals", "search"]


def test_tokenize_list_term_frequency_preserved() -> None:
    """Repeated compound tokens preserve their multiplicity in tokenize_list."""
    tokens = tokenize_list("crm.deals.search crm.deals.search")
    # Each occurrence contributes the compound + 3 segments → 4 entries × 2 = 8
    assert tokens.count("crm.deals.search") == 2
    assert tokens.count("crm") == 2
    assert tokens.count("deals") == 2
    assert tokens.count("search") == 2


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

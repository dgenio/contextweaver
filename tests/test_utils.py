"""Tests for contextweaver._utils -- tokenize, jaccard, TfIdfScorer."""

from __future__ import annotations

import pytest

from contextweaver._utils import STOPWORDS, TfIdfScorer, jaccard, tokenize


class TestStopwords:
    """Tests for the STOPWORDS constant."""

    def test_nonempty(self) -> None:
        assert len(STOPWORDS) >= 50

    def test_contains_common_words(self) -> None:
        assert "the" in STOPWORDS
        assert "and" in STOPWORDS
        assert "is" in STOPWORDS

    def test_does_not_contain_content_words(self) -> None:
        assert "search" not in STOPWORDS
        assert "database" not in STOPWORDS


class TestTokenize:
    """Tests for the tokenize function."""

    def test_basic_tokenization(self) -> None:
        tokens = tokenize("Search the database quickly!")
        assert "search" in tokens
        assert "database" in tokens
        assert "quickly" in tokens

    def test_stopwords_removed(self) -> None:
        tokens = tokenize("the and is are")
        assert len(tokens) == 0

    def test_filters_short_tokens(self) -> None:
        tokens = tokenize("a bb ccc")
        assert "a" not in tokens
        assert "bb" in tokens
        assert "ccc" in tokens

    def test_returns_set(self) -> None:
        tokens = tokenize("hello hello world")
        assert isinstance(tokens, set)
        assert len(tokens) == 2

    def test_empty_string(self) -> None:
        tokens = tokenize("")
        assert tokens == set()

    def test_lowercase(self) -> None:
        tokens = tokenize("HELLO World")
        assert "hello" in tokens
        assert "world" in tokens
        assert "HELLO" not in tokens


class TestJaccard:
    """Tests for the jaccard similarity function."""

    def test_identical_sets(self) -> None:
        a = {"x", "y", "z"}
        assert jaccard(a, a) == pytest.approx(1.0)

    def test_disjoint_sets(self) -> None:
        assert jaccard({"a"}, {"b"}) == pytest.approx(0.0)

    def test_empty_sets(self) -> None:
        assert jaccard(set(), set()) == pytest.approx(0.0)

    def test_partial_overlap(self) -> None:
        a = {"a", "b", "c"}
        b = {"b", "c", "d"}
        assert jaccard(a, b) == pytest.approx(2 / 4)

    def test_one_empty(self) -> None:
        assert jaccard(set(), {"a", "b"}) == pytest.approx(0.0)


class TestTfIdfScorer:
    """Tests for the TfIdfScorer class."""

    def test_fit_returns_self(self) -> None:
        scorer = TfIdfScorer()
        result = scorer.fit(["hello world", "goodbye world"])
        assert result is scorer

    def test_score_single_doc(self) -> None:
        scorer = TfIdfScorer().fit(["search database quickly"])
        score = scorer.score("search database", 0)
        assert isinstance(score, float)
        assert score > 0.0

    def test_score_all_returns_sorted(self) -> None:
        docs = [
            "search database quickly",
            "fast database access",
            "unrelated content here",
        ]
        scorer = TfIdfScorer().fit(docs)
        scores = scorer.score_all("fast database")
        assert len(scores) == 3
        # Scores should be descending
        for i in range(len(scores) - 1):
            assert scores[i][1] >= scores[i + 1][1]

    def test_empty_corpus(self) -> None:
        scorer = TfIdfScorer().fit([])
        assert scorer.score_all("hello") == []

    def test_score_out_of_range(self) -> None:
        scorer = TfIdfScorer().fit(["hello world"])
        with pytest.raises(IndexError):
            scorer.score("hello", 5)

    def test_negative_index_raises(self) -> None:
        scorer = TfIdfScorer().fit(["hello world"])
        with pytest.raises(IndexError):
            scorer.score("hello", -1)

    def test_deterministic(self) -> None:
        docs = ["alpha beta gamma", "delta epsilon", "alpha delta"]
        s1 = TfIdfScorer().fit(docs)
        s2 = TfIdfScorer().fit(docs)
        assert s1.score_all("alpha delta") == s2.score_all("alpha delta")

    def test_query_with_only_stopwords(self) -> None:
        scorer = TfIdfScorer().fit(["hello world"])
        score = scorer.score("the and is", 0)
        assert score == 0.0

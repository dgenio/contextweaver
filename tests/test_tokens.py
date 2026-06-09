"""Tests for the built-in token counter (issue #405)."""

from __future__ import annotations

from contextweaver import tokens
from contextweaver.protocols import CharDivFourEstimator, TokenEstimator


def test_count_returns_non_negative_int() -> None:
    assert isinstance(tokens.count("hello world"), int)
    assert tokens.count("hello world") >= 0


def test_count_empty_string_is_zero() -> None:
    assert tokens.count("") == 0


def test_count_is_monotonic_in_length() -> None:
    short = tokens.count("one two")
    long = tokens.count("one two three four five six seven eight nine ten")
    assert long > short


def test_count_matches_its_counter() -> None:
    # count() must agree with the counter get_token_counter() hands out, so the
    # firewall/BuildStats numbers match what callers measure (#405).
    text = "the quick brown fox jumps over the lazy dog"
    assert tokens.count(text) == tokens.get_token_counter().estimate(text)


def test_get_token_counter_is_cached() -> None:
    assert tokens.get_token_counter() is tokens.get_token_counter()
    assert tokens.get_token_counter("gpt-4o") is tokens.get_token_counter("gpt-4o")


def test_get_token_counter_is_a_token_estimator() -> None:
    assert isinstance(tokens.get_token_counter(), TokenEstimator)


def test_heuristic_counter_is_dependency_free_and_exact() -> None:
    counter = tokens.heuristic_counter()
    assert isinstance(counter, CharDivFourEstimator)
    # The heuristic is exactly len // 4 — a byte-deterministic, offline count.
    assert counter.estimate("x" * 40) == 10


def test_token_counter_alias_is_token_estimator() -> None:
    assert tokens.TokenCounter is TokenEstimator

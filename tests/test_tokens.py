"""Tests for the built-in token counter (issue #405)."""

from __future__ import annotations

from contextweaver import tokens
from contextweaver.protocols import (
    CharDivFourEstimator,
    HeuristicEstimator,
    TokenEstimator,
)


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


def test_heuristic_counter_is_script_aware() -> None:
    counter = tokens.heuristic_counter()
    # heuristic_counter() now returns the script-aware HeuristicEstimator (#525).
    assert isinstance(counter, HeuristicEstimator)
    # Latin/ASCII stays byte-identical to the old len // 4 default.
    assert counter.estimate("x" * 40) == 10
    # CJK is counted at ~1 token/char rather than ~0.25 (the ~4x under-count fix).
    assert counter.estimate("世界" * 20) == 40


def test_token_counter_alias_is_token_estimator() -> None:
    assert tokens.TokenCounter is TokenEstimator


# ---------------------------------------------------------------------------
# Estimator registry + observability (issue #493)
# ---------------------------------------------------------------------------


def test_register_estimator_resolves_by_name() -> None:
    sentinel = CharDivFourEstimator()
    tokens.register_estimator("unit-test-provider", sentinel)
    try:
        assert tokens.get_token_counter("unit-test-provider") is sentinel
        assert tokens.registered_estimators()["unit-test-provider"] is sentinel
    finally:
        tokens._REGISTRY.pop("unit-test-provider", None)


def test_register_estimator_last_write_wins() -> None:
    first = CharDivFourEstimator()
    second = HeuristicEstimator()
    tokens.register_estimator("dup-provider", first)
    tokens.register_estimator("dup-provider", second)
    try:
        assert tokens.get_token_counter("dup-provider") is second
    finally:
        tokens._REGISTRY.pop("dup-provider", None)


def test_unknown_name_falls_back_to_tiktoken_default() -> None:
    # A name that is not registered resolves to the tiktoken-backed default,
    # which is cached per encoding (so the same instance comes back).
    counter = tokens.get_token_counter("not-registered-anywhere")
    assert isinstance(counter, TokenEstimator)


def test_estimator_name_prefers_registered_name() -> None:
    est = CharDivFourEstimator()
    tokens.register_estimator("named-provider", est)
    try:
        # Registered name wins over the instance's own ``name`` attribute.
        assert tokens.estimator_name(est) == "named-provider"
    finally:
        tokens._REGISTRY.pop("named-provider", None)


def test_estimator_name_uses_instance_name_attribute() -> None:
    assert tokens.estimator_name(HeuristicEstimator()) == "heuristic/v2"
    assert tokens.estimator_name(CharDivFourEstimator()) == "heuristic/chardiv4"


def test_estimator_name_default_counter_is_attributed() -> None:
    # The out-of-the-box counter reports a tiktoken/* path when the encoding is
    # available, or the heuristic fallback name when offline — never empty.
    name = tokens.estimator_name(tokens.get_token_counter())
    assert name.startswith("tiktoken/") or name == "heuristic/v2"

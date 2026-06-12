"""Tests for contextweaver.adapters.gateway_controls.

Deterministic mechanisms behind the gateway dispatch-path controls: the retry
loop (#529), per-session rate limiter (#482), and read-only response cache
(#512).  Every test injects a clock/sleeper so no wall-clock time is consumed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from contextweaver.adapters.gateway_controls import (
    RateLimiter,
    ToolResultCache,
    call_with_retry,
)
from contextweaver.adapters.gateway_error import classify_upstream_exception
from contextweaver.adapters.gateway_policy import RateLimit, RateLimitPolicy, RetryPolicy
from contextweaver.envelope import ResultEnvelope

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Clock:
    """A manually-advanced monotonic clock."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class _RecordingSleeper:
    """An awaitable sleeper that records requested delays instead of sleeping."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def _flaky_call(failures: int, exc: Exception) -> Callable[[], Awaitable[dict[str, Any]]]:
    """Return an async call that raises *exc* the first *failures* times."""
    state = {"n": 0}

    async def _call() -> dict[str, Any]:
        if state["n"] < failures:
            state["n"] += 1
            raise exc
        return {"content": [{"type": "text", "text": "ok"}], "isError": False}

    return _call


def _envelope(summary: str = "hello") -> ResultEnvelope:
    return ResultEnvelope(status="ok", summary=summary)


# ---------------------------------------------------------------------------
# call_with_retry (#529)
# ---------------------------------------------------------------------------


async def test_retry_succeeds_on_first_attempt() -> None:
    sleeper = _RecordingSleeper()
    outcome = await call_with_retry(
        _flaky_call(0, ConnectionError("down")),
        policy=RetryPolicy(max_attempts=3, base_delay=1.0, max_delay=10.0),
        classify=classify_upstream_exception,
        sleep=sleeper,
    )
    assert outcome.error is None
    assert outcome.attempts == 1
    assert sleeper.delays == []


async def test_retry_recovers_within_budget_with_backoff_schedule() -> None:
    sleeper = _RecordingSleeper()
    outcome = await call_with_retry(
        _flaky_call(2, ConnectionError("transient")),
        policy=RetryPolicy(max_attempts=3, base_delay=1.0, max_delay=10.0),
        classify=classify_upstream_exception,
        sleep=sleeper,
    )
    assert outcome.error is None
    assert outcome.attempts == 3
    # Two backoffs before the third (successful) attempt: 1.0, then 2.0.
    assert sleeper.delays == [1.0, 2.0]


async def test_retry_exhausts_and_returns_last_error() -> None:
    sleeper = _RecordingSleeper()
    outcome = await call_with_retry(
        _flaky_call(99, ConnectionError("still down")),
        policy=RetryPolicy(max_attempts=3, base_delay=1.0, max_delay=10.0),
        classify=classify_upstream_exception,
        sleep=sleeper,
    )
    assert isinstance(outcome.error, ConnectionError)
    assert outcome.attempts == 3
    assert sleeper.delays == [1.0, 2.0]


async def test_retry_does_not_retry_non_retryable_errors() -> None:
    sleeper = _RecordingSleeper()
    # A generic ValueError classifies as UPSTREAM_ERROR (retryable=False).
    outcome = await call_with_retry(
        _flaky_call(99, ValueError("bad request")),
        policy=RetryPolicy(max_attempts=5, base_delay=1.0, max_delay=10.0),
        classify=classify_upstream_exception,
        sleep=sleeper,
    )
    assert isinstance(outcome.error, ValueError)
    assert outcome.attempts == 1
    assert sleeper.delays == []


async def test_retry_single_attempt_policy_is_one_call() -> None:
    sleeper = _RecordingSleeper()
    outcome = await call_with_retry(
        _flaky_call(99, ConnectionError("down")),
        policy=RetryPolicy(),  # max_attempts=1
        classify=classify_upstream_exception,
        sleep=sleeper,
    )
    assert outcome.attempts == 1
    assert sleeper.delays == []


# ---------------------------------------------------------------------------
# RateLimiter (#482)
# ---------------------------------------------------------------------------


def test_rate_limiter_per_minute_sliding_window() -> None:
    clock = _Clock()
    limiter = RateLimiter(
        RateLimitPolicy(per_meta_tool={"tool_execute": RateLimit(max_calls_per_minute=2)}),
        clock=clock,
    )
    assert limiter.check("tool_execute").allowed
    assert limiter.check("tool_execute").allowed
    denied = limiter.check("tool_execute")
    assert not denied.allowed
    assert denied.scope == "tool_execute/minute"
    assert denied.retry_after is not None and 0 < denied.retry_after <= 60
    # After the window rolls past 60s the calls age out.
    clock.t = 61.0
    assert limiter.check("tool_execute").allowed


def test_rate_limiter_per_session_never_resets() -> None:
    clock = _Clock()
    limiter = RateLimiter(
        RateLimitPolicy(per_meta_tool={"tool_view": RateLimit(max_calls_per_session=1)}),
        clock=clock,
    )
    assert limiter.check("tool_view").allowed
    denied = limiter.check("tool_view")
    assert not denied.allowed
    assert denied.scope == "tool_view/session"
    assert denied.retry_after is None
    clock.t = 10_000.0
    assert not limiter.check("tool_view").allowed


def test_rate_limiter_dry_run_check_does_not_consume() -> None:
    limiter = RateLimiter(
        RateLimitPolicy(per_meta_tool={"tool_execute": RateLimit(max_calls_per_session=1)})
    )
    # record=False can be called repeatedly without consuming the quota.
    assert limiter.check("tool_execute", record=False).allowed
    assert limiter.check("tool_execute", record=False).allowed
    # The real (recording) call is still permitted.
    assert limiter.check("tool_execute", record=True).allowed
    assert not limiter.check("tool_execute", record=True).allowed


def test_rate_limiter_partial_breach_does_not_consume_other_buckets() -> None:
    limiter = RateLimiter(
        RateLimitPolicy(
            per_meta_tool={"tool_execute": RateLimit(max_calls_per_session=2)},
            per_tool={"x": RateLimit(max_calls_per_session=1)},
        )
    )
    assert limiter.check("tool_execute", tool_id="x").allowed  # meta=1, x=1
    # x is now exhausted; this denial must not consume the meta-tool quota.
    assert not limiter.check("tool_execute", tool_id="x").allowed
    # Two more calls on a different tool prove only one meta slot was used.
    assert limiter.check("tool_execute", tool_id="y").allowed  # meta=2
    assert not limiter.check("tool_execute", tool_id="y").allowed


def test_rate_limiter_no_policy_allows_everything() -> None:
    limiter = RateLimiter(RateLimitPolicy())
    for _ in range(100):
        assert limiter.check("tool_execute", tool_id="anything").allowed


# ---------------------------------------------------------------------------
# ToolResultCache (#512)
# ---------------------------------------------------------------------------


def test_cache_key_is_argument_order_insensitive() -> None:
    a = ToolResultCache.key("t:1", {"a": 1, "b": 2})
    b = ToolResultCache.key("t:1", {"b": 2, "a": 1})
    assert a == b and a is not None
    assert ToolResultCache.key("t:1", {"a": 1}) != a


def test_cache_key_none_for_unserialisable_args() -> None:
    assert ToolResultCache.key("t:1", {"x": object()}) is None


def test_cache_hit_returns_isolated_copy() -> None:
    cache = ToolResultCache(ttl_seconds=60.0, max_entries=8)
    key = ToolResultCache.key("t:1", {"a": 1})
    assert key is not None
    cache.put(key, _envelope("original"))
    got = cache.get(key)
    assert got is not None and got.summary == "original"
    # Mutating the returned copy must not corrupt the cached entry.
    got.summary = "mutated"
    got.facts.append("leak")
    again = cache.get(key)
    assert again is not None
    assert again.summary == "original"
    assert again.facts == []


def test_cache_respects_ttl_with_injected_clock() -> None:
    clock = _Clock()
    cache = ToolResultCache(ttl_seconds=30.0, max_entries=8, clock=clock)
    key = ToolResultCache.key("t:1", {"a": 1})
    assert key is not None
    cache.put(key, _envelope())
    clock.t = 29.0
    assert cache.get(key) is not None
    clock.t = 30.0
    assert cache.get(key) is None  # expired


def test_cache_evicts_least_recently_used() -> None:
    cache = ToolResultCache(ttl_seconds=60.0, max_entries=2)
    k1 = ToolResultCache.key("t", {"n": 1})
    k2 = ToolResultCache.key("t", {"n": 2})
    k3 = ToolResultCache.key("t", {"n": 3})
    assert k1 and k2 and k3
    cache.put(k1, _envelope("1"))
    cache.put(k2, _envelope("2"))
    cache.get(k1)  # touch k1 so k2 becomes the LRU victim
    cache.put(k3, _envelope("3"))
    assert cache.get(k2) is None
    assert cache.get(k1) is not None
    assert cache.get(k3) is not None


def test_cache_invalidate_all_and_admits() -> None:
    cache = ToolResultCache(ttl_seconds=60.0, max_entries=8, allow=frozenset({"ok"}))
    assert cache.admits("ok") is True
    assert cache.admits("nope") is False
    key = ToolResultCache.key("ok", {"a": 1})
    assert key is not None
    cache.put(key, _envelope())
    assert len(cache) == 1
    cache.invalidate_all()
    assert len(cache) == 0
    # A cache with no allow-list admits any tool_id.
    assert ToolResultCache(ttl_seconds=1.0, max_entries=1).admits("anything") is True

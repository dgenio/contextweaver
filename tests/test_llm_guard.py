"""Tests for contextweaver.extras.llm_guard (issue #494).

The guard wraps a plain-Python ``call_fn`` stub, so every test runs in the
default install.  Coverage spans policy validation and serde round-trips,
call-cap rejection, the consecutive-failure circuit breaker (open, half-open
trial after cooldown via an injectable clock, re-open on trial failure),
timeout accounting in both post-hoc and thread-enforced modes, stats
counting, and the degrade-safely contract with ``LlmSummarizer``.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest

from contextweaver.exceptions import ConfigError, PolicyViolationError
from contextweaver.extras.llm_guard import GuardedCallFn, GuardPolicy, GuardStats
from contextweaver.extras.llm_summarizer import LlmSummarizer


class FakeClock:
    """A manually-advanced monotonic clock."""

    def __init__(self) -> None:
        self.now = 0.0

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def __call__(self) -> float:
        return self.now


class ScriptedFn:
    """A call_fn that replays a fixed script of completions / exceptions."""

    def __init__(self, outcomes: list[str | Exception]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, prompt: str) -> str:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _failing(_: str) -> str:
    raise RuntimeError("model unavailable")


def _timed_fn(clock: FakeClock, seconds: float, result: str) -> Callable[[str], str]:
    """A call_fn that advances *clock* by *seconds* before returning *result*."""

    def call(prompt: str) -> str:
        clock.advance(seconds)
        return result

    return call


# ---------------------------------------------------------------------------
# GuardPolicy
# ---------------------------------------------------------------------------


def test_policy_defaults() -> None:
    policy = GuardPolicy()
    assert policy.timeout_seconds == 30.0
    assert policy.max_calls is None
    assert policy.circuit_breaker_threshold == 5
    assert policy.circuit_breaker_cooldown_seconds == 60.0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout_seconds": 0.0},
        {"timeout_seconds": -1.0},
        {"max_calls": 0},
        {"circuit_breaker_threshold": 0},
        {"circuit_breaker_cooldown_seconds": -0.1},
    ],
)
def test_policy_rejects_invalid_values(kwargs: dict[str, object]) -> None:
    with pytest.raises(ConfigError):
        GuardPolicy(**kwargs)  # type: ignore[arg-type]


def test_policy_to_dict_from_dict_round_trip() -> None:
    policy = GuardPolicy(
        timeout_seconds=None,
        max_calls=10,
        circuit_breaker_threshold=3,
        circuit_breaker_cooldown_seconds=5.0,
    )
    assert GuardPolicy.from_dict(policy.to_dict()) == policy


def test_policy_from_dict_defaults_and_errors() -> None:
    assert GuardPolicy.from_dict({}) == GuardPolicy()
    with pytest.raises(ConfigError):
        GuardPolicy.from_dict([])  # type: ignore[arg-type]
    with pytest.raises(ConfigError):
        GuardPolicy.from_dict({"max_calls": "many"})
    with pytest.raises(ConfigError):
        GuardPolicy.from_dict({"timeout_seconds": True})


def test_stats_to_dict() -> None:
    stats = GuardStats(calls_attempted=3, calls_succeeded=2, calls_failed=1, circuit_open=True)
    assert stats.to_dict() == {
        "calls_attempted": 3,
        "calls_succeeded": 2,
        "calls_failed": 1,
        "calls_rejected": 0,
        "timeouts": 0,
        "circuit_open": True,
    }


# ---------------------------------------------------------------------------
# Call cap
# ---------------------------------------------------------------------------


def test_call_cap_rejects_after_max_calls() -> None:
    guard = GuardedCallFn(lambda p: "ok", GuardPolicy(max_calls=2), clock=FakeClock())
    assert guard("a") == "ok"
    assert guard("b") == "ok"
    with pytest.raises(PolicyViolationError) as excinfo:
        guard("c")
    assert excinfo.value.code == "CW_POLICY_VIOLATION"
    assert guard.stats.calls_attempted == 2
    assert guard.stats.calls_succeeded == 2
    assert guard.stats.calls_rejected == 1


def test_failed_calls_count_against_the_cap() -> None:
    guard = GuardedCallFn(_failing, GuardPolicy(max_calls=1), clock=FakeClock())
    with pytest.raises(RuntimeError):
        guard("a")
    with pytest.raises(PolicyViolationError):
        guard("b")
    assert guard.stats.calls_attempted == 1
    assert guard.stats.calls_rejected == 1


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


def test_wrapped_exception_propagates_unchanged() -> None:
    guard = GuardedCallFn(_failing, clock=FakeClock())
    with pytest.raises(RuntimeError, match="model unavailable"):
        guard("x")
    assert guard.stats.calls_failed == 1
    assert not guard.stats.circuit_open


def test_circuit_opens_after_consecutive_failures() -> None:
    clock = FakeClock()
    guard = GuardedCallFn(_failing, GuardPolicy(circuit_breaker_threshold=3), clock=clock)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            guard("x")
    assert guard.stats.circuit_open
    # While open, calls are rejected without reaching call_fn.
    with pytest.raises(PolicyViolationError):
        guard("x")
    assert guard.stats.calls_attempted == 3
    assert guard.stats.calls_failed == 3
    assert guard.stats.calls_rejected == 1


def test_success_resets_the_consecutive_failure_count() -> None:
    fn = ScriptedFn([RuntimeError("a"), RuntimeError("b"), "ok", RuntimeError("c")])
    guard = GuardedCallFn(fn, GuardPolicy(circuit_breaker_threshold=3), clock=FakeClock())
    for _ in range(2):
        with pytest.raises(RuntimeError):
            guard("x")
    assert guard("x") == "ok"
    with pytest.raises(RuntimeError):
        guard("x")
    assert not guard.stats.circuit_open  # only 1 consecutive failure after the success


def test_circuit_closes_after_cooldown_on_successful_trial() -> None:
    clock = FakeClock()
    fn = ScriptedFn([RuntimeError("a"), RuntimeError("b"), "recovered", "ok"])
    policy = GuardPolicy(circuit_breaker_threshold=2, circuit_breaker_cooldown_seconds=60.0)
    guard = GuardedCallFn(fn, policy, clock=clock)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            guard("x")
    assert guard.stats.circuit_open
    clock.advance(59.9)
    with pytest.raises(PolicyViolationError):  # still cooling down
        guard("x")
    clock.advance(0.2)
    assert guard("x") == "recovered"  # half-open trial call succeeds
    assert not guard.stats.circuit_open
    assert guard("x") == "ok"  # circuit stays closed
    assert guard.stats.calls_rejected == 1


def test_failed_half_open_trial_reopens_the_circuit() -> None:
    clock = FakeClock()
    policy = GuardPolicy(circuit_breaker_threshold=2, circuit_breaker_cooldown_seconds=10.0)
    guard = GuardedCallFn(_failing, policy, clock=clock)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            guard("x")
    clock.advance(10.1)
    with pytest.raises(RuntimeError):  # trial call reaches call_fn and fails
        guard("x")
    assert guard.stats.circuit_open
    clock.advance(5.0)  # fresh cooldown window: still open
    with pytest.raises(PolicyViolationError):
        guard("x")


# ---------------------------------------------------------------------------
# Timeout accounting
# ---------------------------------------------------------------------------


def test_post_hoc_timeout_records_stat_but_returns_result() -> None:
    clock = FakeClock()
    guard = GuardedCallFn(
        _timed_fn(clock, 31.0, "slow but fine"), GuardPolicy(timeout_seconds=30.0), clock=clock
    )
    assert guard("x") == "slow but fine"
    assert guard.stats.timeouts == 1
    assert guard.stats.calls_succeeded == 1
    assert guard.stats.calls_failed == 0


def test_fast_call_records_no_timeout() -> None:
    clock = FakeClock()
    guard = GuardedCallFn(
        _timed_fn(clock, 1.0, "fast"), GuardPolicy(timeout_seconds=30.0), clock=clock
    )
    assert guard("x") == "fast"
    assert guard.stats.timeouts == 0


def test_timeout_none_disables_accounting() -> None:
    clock = FakeClock()
    guard = GuardedCallFn(
        _timed_fn(clock, 999.0, "ok"), GuardPolicy(timeout_seconds=None), clock=clock
    )
    assert guard("x") == "ok"
    assert guard.stats.timeouts == 0


def test_thread_enforced_timeout_raises_and_counts_as_failure() -> None:
    def slow(_: str) -> str:
        time.sleep(0.5)
        return "too late"

    guard = GuardedCallFn(slow, GuardPolicy(timeout_seconds=0.05), enforce_timeout_with_thread=True)
    with pytest.raises(PolicyViolationError):
        guard("x")
    assert guard.stats.timeouts == 1
    assert guard.stats.calls_failed == 1
    assert guard.stats.calls_succeeded == 0


def test_thread_enforced_fast_call_succeeds() -> None:
    guard = GuardedCallFn(
        lambda p: "quick", GuardPolicy(timeout_seconds=5.0), enforce_timeout_with_thread=True
    )
    assert guard("x") == "quick"
    assert guard.stats.calls_succeeded == 1
    assert guard.stats.timeouts == 0


# ---------------------------------------------------------------------------
# Degrade-safely contract
# ---------------------------------------------------------------------------


def test_llm_summarizer_falls_back_when_the_guard_rejects() -> None:
    guard = GuardedCallFn(lambda p: "LLM SUMMARY", GuardPolicy(max_calls=1), clock=FakeClock())
    summ = LlmSummarizer(guard)
    assert summ.summarize("raw output") == "LLM SUMMARY"
    # Cap reached: the guard rejects, and the summariser degrades to its
    # deterministic rule-based fallback instead of surfacing the error.
    assert summ.summarize("") == "(empty)"
    assert guard.stats.calls_rejected == 1

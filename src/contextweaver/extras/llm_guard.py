"""Guard envelope for user-supplied LLM ``call_fn`` callables (issue #494).

The LLM-backed plugins (:mod:`contextweaver.extras.llm_summarizer`, the
consolidation merge hook) take a bare ``call_fn: Callable[[str], str]``, so a
misbehaving model endpoint — hanging, flapping, or over-called — is invisible
to the library.  :class:`GuardedCallFn` wraps any such callable in a policy
envelope enforcing a call cap, a consecutive-failure circuit breaker, and
timeout accounting, exposing :class:`GuardStats` counters for observability.

**Degrades safely by construction.**  Guard rejections raise
:class:`~contextweaver.exceptions.PolicyViolationError`, and every shipped
consumer of a ``call_fn`` — ``LlmSummarizer.summarize``, ``LlmExtractor.extract``
— already wraps the model call in ``try/except Exception`` and delegates to its
rule-based fallback, so a rejected or failing guarded call simply degrades to
the deterministic answer.

**Timeout enforcement tradeoff.**  ``call_fn`` is synchronous, and Python
cannot interrupt an arbitrary synchronous call without a thread, so the guard
offers two modes:

- **Post-hoc measurement (default).**  The call always runs to completion; the
  guard measures its duration against the injected monotonic ``clock`` and
  increments :attr:`GuardStats.timeouts` when it exceeded
  :attr:`GuardPolicy.timeout_seconds`.  Zero overhead and no orphan threads,
  but caller latency is *not* bounded — the stat is a diagnostic, not a limit.
- **Thread-based hard timeout (``enforce_timeout_with_thread=True``).**  The
  call is dispatched on a single-use ``concurrent.futures`` thread and the
  caller waits at most ``timeout_seconds`` (real wall-clock time — the
  injected ``clock`` cannot speed this up).  This bounds caller latency, but
  the abandoned thread keeps running in the background because threads cannot
  be killed: the underlying request is *not* cancelled, and each timed-out
  call leaks one thread until ``call_fn`` returns.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Any

from contextweaver.exceptions import ConfigError, PolicyViolationError

logger = logging.getLogger("contextweaver.extras")

#: A monotonic clock returning seconds; mirrors ``adapters.gateway_controls.Clock``.
Clock = Callable[[], float]


@dataclass(frozen=True)
class GuardPolicy:
    """Limits enforced by a :class:`GuardedCallFn` (issue #494).

    Attributes:
        timeout_seconds: Per-call timeout in seconds; ``None`` disables
            timeout accounting.  See the module docstring for the post-hoc
            vs thread-based enforcement tradeoff.
        max_calls: Lifetime cap on calls dispatched to the wrapped
            ``call_fn``; ``None`` (default) means unlimited.
        circuit_breaker_threshold: Consecutive failures that open the circuit.
        circuit_breaker_cooldown_seconds: Seconds an open circuit rejects
            calls before permitting a half-open trial call.
    """

    timeout_seconds: float | None = 30.0
    max_calls: int | None = None
    circuit_breaker_threshold: int = 5
    circuit_breaker_cooldown_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ConfigError("GuardPolicy.timeout_seconds must be positive when set")
        if self.max_calls is not None and self.max_calls < 1:
            raise ConfigError("GuardPolicy.max_calls must be >= 1 when set")
        if self.circuit_breaker_threshold < 1:
            raise ConfigError("GuardPolicy.circuit_breaker_threshold must be >= 1")
        if self.circuit_breaker_cooldown_seconds < 0:
            raise ConfigError("GuardPolicy.circuit_breaker_cooldown_seconds must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "timeout_seconds": self.timeout_seconds,
            "max_calls": self.max_calls,
            "circuit_breaker_threshold": self.circuit_breaker_threshold,
            "circuit_breaker_cooldown_seconds": self.circuit_breaker_cooldown_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GuardPolicy:
        """Build from a config mapping; missing keys take the defaults."""
        if not isinstance(data, dict):
            raise ConfigError("guard policy config must be a mapping")
        return cls(
            timeout_seconds=_opt_number(data.get("timeout_seconds", 30.0), "timeout_seconds"),
            max_calls=_opt_number(data.get("max_calls"), "max_calls", integer=True),
            circuit_breaker_threshold=int(data.get("circuit_breaker_threshold", 5)),
            circuit_breaker_cooldown_seconds=float(
                data.get("circuit_breaker_cooldown_seconds", 60.0)
            ),
        )


@dataclass
class GuardStats:
    """Live counters maintained by a :class:`GuardedCallFn`.

    Attributes:
        calls_attempted: Calls actually dispatched to the wrapped ``call_fn``
            (rejected calls are *not* attempted).
        calls_succeeded: Attempted calls that returned normally.
        calls_failed: Attempted calls that raised (including thread-enforced
            hard timeouts).
        calls_rejected: Calls refused before dispatch by the call cap or an
            open circuit.
        timeouts: Calls that exceeded :attr:`GuardPolicy.timeout_seconds`
            (post-hoc measurement or thread-enforced).
        circuit_open: Whether the circuit breaker is currently open.
    """

    calls_attempted: int = 0
    calls_succeeded: int = 0
    calls_failed: int = 0
    calls_rejected: int = 0
    timeouts: int = 0
    circuit_open: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "calls_attempted": self.calls_attempted,
            "calls_succeeded": self.calls_succeeded,
            "calls_failed": self.calls_failed,
            "calls_rejected": self.calls_rejected,
            "timeouts": self.timeouts,
            "circuit_open": self.circuit_open,
        }


class GuardedCallFn:
    """Policy envelope around a user-supplied ``call_fn`` (issue #494).

    Drop-in compatible with every ``call_fn: Callable[[str], str]`` slot —
    e.g. ``LlmSummarizer(GuardedCallFn(my_call_fn))``.  Rejections (call cap
    reached, circuit open, thread-enforced timeout) raise
    :class:`~contextweaver.exceptions.PolicyViolationError`; failures of the
    wrapped callable propagate unchanged.  Both degrade safely in the shipped
    consumers, which catch ``Exception`` and fall back deterministically.

    The circuit breaker opens after
    :attr:`GuardPolicy.circuit_breaker_threshold` *consecutive* failures.
    While open, calls are rejected until the cooldown has elapsed on *clock*,
    after which one half-open trial call is admitted: success closes the
    circuit, failure re-opens it for a fresh cooldown.

    Args:
        call_fn: Callable taking a prompt string and returning the model's
            text completion.  Bring your own — no LLM SDK is imported.
        policy: The :class:`GuardPolicy` to enforce.  Defaults to
            ``GuardPolicy()``.
        clock: Injectable monotonic clock (seconds) used for cooldown expiry
            and post-hoc timeout measurement.  Defaults to ``time.monotonic``,
            mirroring :mod:`contextweaver.adapters.gateway_controls`.
        enforce_timeout_with_thread: When ``True`` (and
            ``policy.timeout_seconds`` is set), dispatch on a single-use
            thread with a hard real-time timeout; see the module docstring
            for the tradeoff.  Defaults to ``False`` (post-hoc measurement).
    """

    def __init__(
        self,
        call_fn: Callable[[str], str],
        policy: GuardPolicy | None = None,
        *,
        clock: Clock = time.monotonic,
        enforce_timeout_with_thread: bool = False,
    ) -> None:
        self._call = call_fn
        self._policy = policy if policy is not None else GuardPolicy()
        self._clock = clock
        self._thread_timeout = enforce_timeout_with_thread
        self._stats = GuardStats()
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    @property
    def policy(self) -> GuardPolicy:
        """The enforced :class:`GuardPolicy`."""
        return self._policy

    @property
    def stats(self) -> GuardStats:
        """The live :class:`GuardStats` counters for this guard."""
        return self._stats

    def __call__(self, prompt: str) -> str:
        """Dispatch *prompt* to the wrapped ``call_fn`` under the policy.

        Raises:
            PolicyViolationError: On cap reached, circuit open, or a
                thread-enforced hard timeout.  Wrapped-``call_fn`` failures
                propagate unchanged.
        """
        self._check_cap()
        self._check_circuit(self._clock())
        self._stats.calls_attempted += 1
        started = self._clock()
        try:
            result = self._dispatch(prompt)
        except Exception:
            self._record_failure()
            raise
        elapsed = self._clock() - started
        timeout = self._policy.timeout_seconds
        if timeout is not None and elapsed > timeout:
            self._stats.timeouts += 1
            logger.warning(
                "GuardedCallFn: call took %.3fs, exceeding timeout_seconds=%.3f", elapsed, timeout
            )
        self._record_success()
        return result

    def _dispatch(self, prompt: str) -> str:
        """Run the wrapped callable, on a timeout-bounded thread when configured."""
        timeout = self._policy.timeout_seconds
        if not self._thread_timeout or timeout is None:
            return self._call(prompt)
        # No context manager: ``__exit__`` would block on the running call,
        # defeating the timeout (module docstring covers the leaked worker).
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cw-llm-guard")
        try:
            future = pool.submit(self._call, prompt)
            try:
                return future.result(timeout=timeout)
            except FutureTimeoutError:
                self._stats.timeouts += 1
                raise PolicyViolationError(
                    f"LLM call exceeded the hard timeout of {timeout}s",
                    hint="raise GuardPolicy.timeout_seconds or disable enforce_timeout_with_thread",
                ) from None
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    def _check_cap(self) -> None:
        max_calls = self._policy.max_calls
        if max_calls is not None and self._stats.calls_attempted >= max_calls:
            self._stats.calls_rejected += 1
            raise PolicyViolationError(
                f"LLM call cap reached ({max_calls} calls)",
                hint="raise GuardPolicy.max_calls or construct a fresh GuardedCallFn",
            )

    def _check_circuit(self, now: float) -> None:
        if not self._stats.circuit_open or self._opened_at is None:
            return
        remaining = self._policy.circuit_breaker_cooldown_seconds - (now - self._opened_at)
        if remaining > 0:
            self._stats.calls_rejected += 1
            raise PolicyViolationError(
                f"LLM circuit breaker is open for another {remaining:.1f}s "
                f"after {self._consecutive_failures} consecutive failures",
                hint="wait for the cooldown or fix the failing call_fn endpoint",
            )
        # Cooldown elapsed: admit one half-open trial call (success closes,
        # failure re-opens with a fresh cooldown window).

    def _record_failure(self) -> None:
        self._stats.calls_failed += 1
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._policy.circuit_breaker_threshold:
            self._stats.circuit_open = True
            self._opened_at = self._clock()
            logger.warning(
                "GuardedCallFn: circuit opened after %d consecutive failures",
                self._consecutive_failures,
            )

    def _record_success(self) -> None:
        self._stats.calls_succeeded += 1
        self._consecutive_failures = 0
        if self._stats.circuit_open:
            self._stats.circuit_open = False
            self._opened_at = None


def _opt_number(value: object, label: str, *, integer: bool = False) -> Any:  # noqa: ANN401
    """Coerce an optional numeric config value (``None`` passes through; caller narrows)."""
    if value is None:
        return None
    accepted = (int, str) if integer else (int, float, str)
    try:
        if isinstance(value, bool) or not isinstance(value, accepted):
            raise ValueError
        return int(value) if integer else float(value)
    except ValueError:
        kind = "an integer" if integer else "a number"
        raise ConfigError(f"guard.{label} must be {kind} or null, got {value!r}") from None

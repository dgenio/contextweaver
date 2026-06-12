"""Runtime mechanisms for the gateway dispatch-path controls.

The deterministic, individually-testable machinery behind the config types in
:mod:`contextweaver.adapters.gateway_policy`:

- :func:`call_with_retry` — drives bounded exponential-backoff retries around an
  upstream dispatch coroutine (issue #529).
- :class:`RateLimiter` — per-session sliding-window + cumulative invocation
  counters for the gateway meta-tools (issue #482).
- :class:`ToolResultCache` — opt-in, TTL- and size-bounded cache of read-only
  ``tool_execute`` results (issue #512).

Every component takes an injectable ``clock`` (and :func:`call_with_retry` an
injectable ``sleep``) so the backoff schedule, window expiry, and TTL are
exercisable in tests without real time.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from contextweaver.adapters.gateway_policy import RateLimit, RateLimitPolicy, RetryPolicy
from contextweaver.envelope import ResultEnvelope
from contextweaver.exceptions import ConfigError

#: A monotonic clock returning seconds.  ``time.monotonic`` by default.
Clock = Callable[[], float]

#: An awaitable sleep taking a delay in seconds.  ``asyncio.sleep`` by default.
Sleeper = Callable[[float], Awaitable[None]]

#: Classifier mapping an upstream exception to ``(code, retryable)``.
Classifier = Callable[[BaseException], "tuple[str, bool]"]


@dataclass
class RetryOutcome:
    """Result of :func:`call_with_retry`.

    Exactly one of :attr:`raw` / :attr:`error` is set.  :attr:`attempts` counts
    dispatch attempts actually made (always ``>= 1``).
    """

    raw: dict[str, Any] | None
    error: BaseException | None
    attempts: int


async def call_with_retry(
    call: Callable[[], Awaitable[dict[str, Any]]],
    *,
    policy: RetryPolicy,
    classify: Classifier,
    sleep: Sleeper,
    jitter_source: Callable[[], float] | None = None,
) -> RetryOutcome:
    """Invoke *call*, retrying transient failures per *policy*.

    The first failure is retried only when *classify* reports it retryable
    *and* its code is in :attr:`RetryPolicy.retryable_codes`, up to
    :attr:`RetryPolicy.max_attempts` total attempts.  Successful calls and
    non-retryable failures return immediately.  Any ``Exception`` raised by
    *call* is captured and, once retries are exhausted or the failure is
    non-retryable, returned on :attr:`RetryOutcome.error` so the caller keeps
    its single error-to-``GatewayError`` mapping.  ``BaseException`` subclasses
    that are not ``Exception`` — notably :class:`asyncio.CancelledError` — are
    intentionally **not** caught and propagate immediately, so cancellation
    during backoff is prompt.

    Args:
        call: Zero-arg coroutine performing one upstream dispatch.
        policy: The retry policy.
        classify: Maps an exception to ``(code, retryable)``.
        sleep: Awaitable sleep used for backoff (injected in tests).
        jitter_source: Optional ``() -> float in [0, 1)`` supplying the jitter
            fraction per delay.  Omitted ⇒ no jitter (deterministic schedule).
    """
    attempts = 0
    while True:
        attempts += 1
        try:
            raw = await call()
            return RetryOutcome(raw=raw, error=None, attempts=attempts)
        except Exception as exc:  # noqa: BLE001 — re-surfaced via RetryOutcome.error
            code, retryable = classify(exc)
            exhausted = attempts >= policy.max_attempts
            if exhausted or not retryable or code not in policy.retryable_codes:
                return RetryOutcome(raw=None, error=exc, attempts=attempts)
            fraction = jitter_source() if jitter_source is not None else 0.0
            await sleep(policy.backoff_delay(attempts - 1, fraction))


@dataclass
class RateLimitDecision:
    """Outcome of a :meth:`RateLimiter.check` call.

    Attributes:
        allowed: Whether the call is permitted.
        retry_after: Seconds until the call would be permitted, when known
            (per-minute window); ``None`` for an exhausted per-session quota or
            when allowed.
        scope: Which limit denied the call (e.g. ``"tool_execute/minute"``);
            empty when allowed.
    """

    allowed: bool
    retry_after: float | None = None
    scope: str = ""


class RateLimiter:
    """Per-session invocation counters enforcing a :class:`RateLimitPolicy` (#482).

    One instance belongs to one :class:`~contextweaver.adapters.proxy_runtime.ProxyRuntime`
    (one session).  Counters are deterministic under an injected *clock*.
    """

    def __init__(self, policy: RateLimitPolicy, *, clock: Clock = time.monotonic) -> None:
        """Create a limiter enforcing *policy* against *clock*."""
        self._policy = policy
        self._clock = clock
        #: Sliding-window timestamps and cumulative counts, keyed by bucket name.
        self._window: dict[str, deque[float]] = {}
        self._session_count: dict[str, int] = {}

    def check(
        self, meta_tool: str, *, tool_id: str | None = None, record: bool = True
    ) -> RateLimitDecision:
        """Evaluate (and optionally record) one invocation of *meta_tool*.

        Both the meta-tool limit and — for ``tool_execute`` — any per-tool limit
        must permit the call.  When *record* is ``False`` the limit is evaluated
        without consuming quota (used for ``dry_run``).

        Args:
            meta_tool: The meta-tool being invoked.
            tool_id: Canonical ``tool_id`` for per-tool limits (execute only).
            record: Whether to count this call against the quota when allowed.

        Returns:
            A :class:`RateLimitDecision`.
        """
        now = self._clock()
        buckets: list[tuple[str, RateLimit]] = []
        meta_limit = self._policy.per_meta_tool.get(meta_tool)
        if meta_limit is not None:
            buckets.append((meta_tool, meta_limit))
        if tool_id is not None:
            tool_limit = self._policy.per_tool.get(tool_id)
            if tool_limit is not None:
                buckets.append((f"tool:{tool_id}", tool_limit))
        # Evaluate every applicable bucket before recording so a partial breach
        # never consumes quota on the buckets that would have allowed it.
        for bucket, limit in buckets:
            decision = self._evaluate(bucket, limit, now)
            if not decision.allowed:
                return decision
        if record:
            for bucket, limit in buckets:
                self._record(bucket, limit, now)
        return RateLimitDecision(allowed=True)

    def _evaluate(self, bucket: str, limit: RateLimit, now: float) -> RateLimitDecision:
        if (
            limit.max_calls_per_session is not None
            and self._session_count.get(bucket, 0) >= limit.max_calls_per_session
        ):
            return RateLimitDecision(allowed=False, retry_after=None, scope=f"{bucket}/session")
        if limit.max_calls_per_minute is not None:
            window = self._fresh_window(bucket, now)
            if len(window) >= limit.max_calls_per_minute:
                retry_after = max(0.0, 60.0 - (now - window[0]))
                return RateLimitDecision(
                    allowed=False, retry_after=retry_after, scope=f"{bucket}/minute"
                )
        return RateLimitDecision(allowed=True)

    def _record(self, bucket: str, limit: RateLimit, now: float) -> None:
        if limit.max_calls_per_session is not None:
            self._session_count[bucket] = self._session_count.get(bucket, 0) + 1
        if limit.max_calls_per_minute is not None:
            self._fresh_window(bucket, now).append(now)

    def _fresh_window(self, bucket: str, now: float) -> deque[float]:
        """Return *bucket*'s timestamp deque with entries older than 60s evicted."""
        window = self._window.setdefault(bucket, deque())
        cutoff = now - 60.0
        while window and window[0] <= cutoff:
            window.popleft()
        return window


class ToolResultCache:
    """TTL- and size-bounded cache of read-only ``tool_execute`` results (#512).

    Caching is opt-in and operator-controlled: the runtime only consults the
    cache for tools whose upstream-declared annotations mark them read-only, and
    only when an :attr:`allow` list (when set) admits the ``tool_id``.  Errors
    and mutating tools are never cached by the runtime.  Entries are evicted
    least-recently-used past :attr:`max_entries` and on TTL expiry; the cache is
    invalidated wholesale on catalog refresh.

    .. warning::
        Read-only eligibility is derived from the upstream ``readOnlyHint``
        annotation (see the SECURITY NOTE in
        :mod:`contextweaver.adapters.mcp`), which is a server-declared,
        **unverified** hint.  Enabling caching with no :attr:`allow` list
        therefore trusts every upstream's self-declaration: a mutating tool that
        falsely declares itself read-only would have its first result cached and
        a second identical call served from cache *without* re-dispatching the
        side effect.  Caching stays off unless the operator opts in, and
        safety-critical deployments should pair ``read_only: true`` with an
        explicit :attr:`allow` list rather than trusting hints globally.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = 60.0,
        max_entries: int = 256,
        allow: frozenset[str] | None = None,
        clock: Clock = time.monotonic,
    ) -> None:
        """Create a cache; *allow* (when set) restricts which tool_ids may cache."""
        if ttl_seconds <= 0:
            raise ConfigError("ToolResultCache.ttl_seconds must be positive")
        if max_entries < 1:
            raise ConfigError("ToolResultCache.max_entries must be >= 1")
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._allow = allow
        self._clock = clock
        self._entries: OrderedDict[str, tuple[float, ResultEnvelope]] = OrderedDict()

    def admits(self, tool_id: str) -> bool:
        """Whether *tool_id* is eligible to cache under this cache's allow-list."""
        return self._allow is None or tool_id in self._allow

    @staticmethod
    def key(tool_id: str, args: dict[str, Any]) -> str | None:
        """Return a stable cache key for ``(tool_id, args)``.

        The key is argument-order-insensitive (canonical JSON).  Returns
        ``None`` when *args* is not JSON-serialisable, signalling the caller to
        skip caching rather than guess at equality.
        """
        try:
            canonical = json.dumps(args, sort_keys=True, separators=(",", ":"))
        except TypeError:
            return None
        digest = hashlib.sha256(f"{tool_id}\x00{canonical}".encode()).hexdigest()
        return digest

    def get(self, key: str) -> ResultEnvelope | None:
        """Return a cached envelope for *key*, or ``None`` on miss/expiry.

        A hit returns a deep copy so callers cannot mutate cached state, and
        refreshes the entry's LRU recency.
        """
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, envelope = entry
        if self._clock() >= expires_at:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return copy.deepcopy(envelope)

    def put(self, key: str, envelope: ResultEnvelope) -> None:
        """Store a deep copy of *envelope* under *key*, evicting LRU past the bound."""
        expires_at = self._clock() + self._ttl
        self._entries[key] = (expires_at, copy.deepcopy(envelope))
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def invalidate_all(self) -> None:
        """Drop every cached entry (called on catalog refresh)."""
        self._entries.clear()

    def __len__(self) -> int:
        """Number of currently-stored entries (including not-yet-evicted expired)."""
        return len(self._entries)

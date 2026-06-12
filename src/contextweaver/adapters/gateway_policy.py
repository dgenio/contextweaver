"""Configuration and result types for the gateway dispatch-path controls.

This module holds the *pure data* surface for the reliability, quota, and
safety controls layered onto :class:`~contextweaver.adapters.proxy_runtime.ProxyRuntime`'s
``tool_execute`` / ``tool_browse`` / ``tool_view`` dispatch path:

- :class:`RetryPolicy` — bounded exponential backoff for transient upstream
  failures (issue #529).
- :class:`RateLimit` / :class:`RateLimitPolicy` — per-session invocation quotas
  on the gateway meta-tools (issue #482).
- :class:`DryRunReport` — the structured report returned by a ``dry_run=True``
  ``tool_execute`` (issue #483).

The matching *behaviour* (counters, clocks, the retry loop, the response cache)
lives in :mod:`contextweaver.adapters.gateway_controls`; keeping the two apart
keeps each module small and the config types trivially serialisable.  All
defaults are inert — an unconfigured runtime behaves exactly as before.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from contextweaver.exceptions import ConfigError

#: Meta-tool names a :class:`RateLimitPolicy` may key limits on (§4.2).
META_TOOL_NAMES: frozenset[str] = frozenset({"tool_browse", "tool_execute", "tool_view"})

#: Default upstream-error codes a :class:`RetryPolicy` treats as retryable.
#: Deliberately transport-only (issue #529): tool-level error *results* and
#: auth/permission failures are never retried because the call may not be
#: idempotent and the outcome will not change on a retry.
DEFAULT_RETRYABLE_CODES: tuple[str, ...] = ("UPSTREAM_TIMEOUT", "UPSTREAM_UNAVAILABLE")


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded exponential-backoff retry policy for upstream dispatch (#529).

    The default (``max_attempts=1``) performs a single attempt — byte-identical
    to the pre-retry behaviour.  Only exceptions that
    :func:`~contextweaver.adapters.gateway_error.classify_upstream_exception`
    maps to a retryable code in :attr:`retryable_codes` are retried; tool-level
    error *results* (the tool ran and returned ``isError=True``) are never
    retried.

    Attributes:
        max_attempts: Total dispatch attempts including the first.  ``1``
            disables retries.
        base_delay: Seconds before the first retry; doubled each subsequent
            attempt.
        max_delay: Upper bound on any single backoff delay, in seconds.
        jitter: Fraction ``[0.0, 1.0]`` of each delay subtracted at random to
            de-correlate retries.  ``0.0`` (default) is fully deterministic.
        retryable_codes: Gateway error codes eligible for retry.
    """

    max_attempts: int = 1
    base_delay: float = 0.1
    max_delay: float = 5.0
    jitter: float = 0.0
    retryable_codes: tuple[str, ...] = DEFAULT_RETRYABLE_CODES

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ConfigError("RetryPolicy.max_attempts must be >= 1")
        if self.base_delay < 0 or self.max_delay < 0:
            raise ConfigError("RetryPolicy delays must be non-negative")
        if not 0.0 <= self.jitter <= 1.0:
            raise ConfigError("RetryPolicy.jitter must be in [0.0, 1.0]")

    @property
    def enabled(self) -> bool:
        """Whether this policy can perform more than one attempt."""
        return self.max_attempts > 1

    def backoff_delay(self, attempt_index: int, jitter_fraction: float = 0.0) -> float:
        """Return the backoff delay before retry *attempt_index* (0-based).

        Args:
            attempt_index: 0 for the delay before the first retry, 1 for the
                next, and so on.
            jitter_fraction: A caller-supplied value in ``[0.0, 1.0)`` (e.g.
                ``random.random()``) used to apply :attr:`jitter`
                deterministically.  Defaults to ``0.0`` so the schedule is
                reproducible in tests.

        Returns:
            The delay in seconds, capped at :attr:`max_delay`.
        """
        raw = self.base_delay * (2.0**attempt_index)
        capped = min(raw, self.max_delay)
        return capped * (1.0 - self.jitter * jitter_fraction)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "max_attempts": self.max_attempts,
            "base_delay": self.base_delay,
            "max_delay": self.max_delay,
            "jitter": self.jitter,
            "retryable_codes": list(self.retryable_codes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RetryPolicy:
        """Build from a config mapping (e.g. a ``mcp serve`` config ``retry`` block)."""
        if not isinstance(data, dict):
            raise ConfigError("retry policy config must be a mapping")
        codes = data.get("retryable_codes")
        return cls(
            max_attempts=int(data.get("max_attempts", 1)),
            base_delay=float(data.get("base_delay", 0.1)),
            max_delay=float(data.get("max_delay", 5.0)),
            jitter=float(data.get("jitter", 0.0)),
            retryable_codes=tuple(codes) if codes is not None else DEFAULT_RETRYABLE_CODES,
        )


@dataclass(frozen=True)
class RateLimit:
    """A single per-session invocation limit (issue #482).

    Both bounds are optional; ``None`` means "no limit on this axis".  The
    per-minute bound is a sliding 60-second window; the per-session bound is a
    cumulative count that never resets within a runtime's lifetime.

    Attributes:
        max_calls_per_minute: Maximum calls in any rolling 60-second window.
        max_calls_per_session: Maximum cumulative calls for the session.
    """

    max_calls_per_minute: int | None = None
    max_calls_per_session: int | None = None

    def __post_init__(self) -> None:
        for label, value in (
            ("max_calls_per_minute", self.max_calls_per_minute),
            ("max_calls_per_session", self.max_calls_per_session),
        ):
            if value is not None and value < 1:
                raise ConfigError(f"RateLimit.{label} must be >= 1 when set")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RateLimit:
        """Build one limit from a config mapping."""
        if not isinstance(data, dict):
            raise ConfigError("rate limit entry must be a mapping")
        return cls(
            max_calls_per_minute=_opt_int(data.get("max_calls_per_minute")),
            max_calls_per_session=_opt_int(data.get("max_calls_per_session")),
        )


@dataclass(frozen=True)
class RateLimitPolicy:
    """Per-meta-tool and per-tool invocation quotas for one session (#482).

    Attributes:
        per_meta_tool: Limit keyed by meta-tool name (``tool_browse`` /
            ``tool_execute`` / ``tool_view``).
        per_tool: Limit keyed by canonical ``tool_id``; only consulted on
            ``tool_execute``.  Applied *in addition* to any meta-tool limit.
    """

    per_meta_tool: dict[str, RateLimit] = field(default_factory=dict)
    per_tool: dict[str, RateLimit] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        """Whether any limit is configured."""
        return bool(self.per_meta_tool or self.per_tool)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RateLimitPolicy:
        """Build from the ``rate_limits`` config block.

        The mapping keys are meta-tool names (plus an optional ``per_tool``
        sub-mapping of canonical ``tool_id`` → limit), mirroring the documented
        ``mcp serve`` config shape.
        """
        if not isinstance(data, dict):
            raise ConfigError("rate_limits config must be a mapping")
        per_meta_tool: dict[str, RateLimit] = {}
        per_tool: dict[str, RateLimit] = {}
        for key, value in data.items():
            if key == "per_tool":
                if not isinstance(value, dict):
                    raise ConfigError("rate_limits.per_tool must be a mapping")
                per_tool = {str(tid): RateLimit.from_dict(v) for tid, v in value.items()}
                continue
            if key not in META_TOOL_NAMES:
                allowed = ", ".join(sorted(META_TOOL_NAMES))
                raise ConfigError(f"unknown rate_limits key {key!r}; allowed: {allowed}, per_tool")
            per_meta_tool[key] = RateLimit.from_dict(value)
        return cls(per_meta_tool=per_meta_tool, per_tool=per_tool)


@dataclass
class DryRunReport:
    """Structured report returned by a ``dry_run=True`` ``tool_execute`` (#483).

    Produced when every pre-dispatch step (hydration, argument validation, quota
    evaluation) is run but the upstream tool is *not* invoked and no artifacts
    are written.

    Attributes:
        tool_id: The resolved canonical ``tool_id``.
        upstream_name: The raw upstream tool name the call would dispatch to.
        args_valid: Whether the arguments passed schema validation.
        annotations: The upstream-declared MCP annotations, always stamped with
            ``verified=False`` because these are unverified upstream hints.
        checks: Ordered list of ``{"name", "status"}`` pre-dispatch check
            outcomes.
    """

    tool_id: str
    upstream_name: str
    args_valid: bool
    annotations: dict[str, Any] = field(default_factory=dict)
    checks: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the §4.2 dry-run wire shape (``dry_run`` is always true)."""
        return {
            "dry_run": True,
            "tool_id": self.tool_id,
            "upstream_name": self.upstream_name,
            "args_valid": self.args_valid,
            "annotations": dict(self.annotations),
            "checks": [dict(c) for c in self.checks],
        }


def _opt_int(value: object) -> int | None:
    """Coerce an optional config value to ``int`` (``None`` passes through)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ConfigError(f"expected an integer, got {value!r}")
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"expected an integer, got {value!r}") from exc

"""Named gateway policy presets bundling authz, retry, quota, and cache config.

Operators tune the MCP gateway's runtime authorization gate
(:mod:`contextweaver.adapters.gateway_authz`) and dispatch-path controls
(:mod:`contextweaver.adapters.gateway_policy`) independently today, which is
a lot of trial-and-error for a first deployment (issue #664). This module adds
three named starting points — :meth:`GatewayPreset.from_preset` mirrors
:meth:`~contextweaver.profiles.ProfileConfig.from_preset` — that an operator
can select via ``mcp serve --policy-preset`` or the ``policy_preset`` config
key, then override with an explicit ``policy`` / ``retry`` / ``rate_limits`` /
``cache`` block. Selecting no preset is inert: behaviour is unchanged.

- ``"safe"`` — every ``tool_execute`` call requires human approval, regardless
  of the (unverified) upstream read-only hint; low quotas; caching off.
- ``"balanced"`` — allow-all authz with moderate quotas; caching off.
- ``"throughput"`` — allow-all, generous retries, no quotas, read-only caching
  on. See the warning on :class:`CacheConfig`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from contextweaver.adapters.gateway_authz import PolicyRule, ToolPolicy
from contextweaver.adapters.gateway_policy import RateLimit, RateLimitPolicy, RetryPolicy
from contextweaver.exceptions import ConfigError

#: Schema id stamped on every :meth:`GatewayPreset.to_dict` output (issue #664).
GATEWAY_PRESET_SCHEMA = "gateway-policy-preset/v1"


@dataclass(frozen=True)
class CacheConfig:
    """Pure-data config for the read-only ``tool_execute`` cache (#512).

    Behaviour lives in :class:`~contextweaver.adapters.gateway_controls.ToolResultCache`.

    .. warning::
        :attr:`read_only` gates caching on the upstream ``readOnlyHint``
        annotation, a server-declared, **unverified** hint. Enabling with no
        :attr:`allow` list trusts every upstream's self-declaration; safety-
        critical deployments should pair ``read_only=True`` with an explicit
        allow-list.

    Attributes:
        read_only: Whether caching is enabled. ``False`` (default) is inert.
        ttl_seconds: Seconds before a cached entry expires.
        max_entries: Maximum entries before least-recently-used eviction.
        allow: Optional ``tool_id`` allow-list; ``None`` trusts every tool.
    """

    read_only: bool = False
    ttl_seconds: float = 60.0
    max_entries: int = 256
    allow: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if self.ttl_seconds <= 0:
            raise ConfigError("CacheConfig.ttl_seconds must be positive")
        if self.max_entries < 1:
            raise ConfigError("CacheConfig.max_entries must be >= 1")
        if self.allow is not None and (
            isinstance(self.allow, str)
            or not isinstance(self.allow, (frozenset, set, list, tuple))
            or not all(isinstance(item, str) for item in self.allow)
        ):
            # A bare string is iterable, so it would otherwise be treated as
            # a per-character allow-list rather than a single tool_id.
            raise ConfigError("CacheConfig.allow must be an iterable of strings")
        if self.allow is not None and not isinstance(self.allow, frozenset):
            # Coerce any accepted string iterable (set/list/tuple) to frozenset
            # so the runtime value matches the ``frozenset[str]`` annotation and
            # the frozen dataclass stays hashable (a list-valued field would make
            # instances unhashable, breaking ``frozen=True``'s contract).
            object.__setattr__(self, "allow", frozenset(self.allow))

    @property
    def enabled(self) -> bool:
        """Whether this config would build a cache."""
        return self.read_only

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a dict mirroring the ``cache`` config shape."""
        return {
            "read_only": self.read_only,
            "ttl_seconds": self.ttl_seconds,
            "max_entries": self.max_entries,
            "allow": sorted(self.allow) if self.allow is not None else None,
        }


def _safe_preset() -> tuple[ToolPolicy, RetryPolicy, RateLimitPolicy, CacheConfig]:
    policy = ToolPolicy(
        rules=[
            PolicyRule(
                action="require_approval",
                meta_tool="tool_execute",
                reason="preset 'safe' requires approval for every tool_execute call",
            )
        ]
    )
    retry = RetryPolicy(max_attempts=2)
    rate_limits = RateLimitPolicy(
        per_meta_tool={"tool_execute": RateLimit(max_calls_per_minute=30)}
    )
    return policy, retry, rate_limits, CacheConfig()


def _balanced_preset() -> tuple[ToolPolicy, RetryPolicy, RateLimitPolicy, CacheConfig]:
    policy = ToolPolicy()
    retry = RetryPolicy(max_attempts=3)
    rate_limits = RateLimitPolicy(
        per_meta_tool={"tool_execute": RateLimit(max_calls_per_minute=120)}
    )
    return policy, retry, rate_limits, CacheConfig()


def _throughput_preset() -> tuple[ToolPolicy, RetryPolicy, RateLimitPolicy, CacheConfig]:
    policy = ToolPolicy()
    retry = RetryPolicy(max_attempts=5, jitter=0.2)
    rate_limits = RateLimitPolicy()
    return policy, retry, rate_limits, CacheConfig(read_only=True)


#: Preset name -> factory building fresh, unshared config objects (issue #664).
_PRESET_FACTORIES: dict[
    str, Callable[[], tuple[ToolPolicy, RetryPolicy, RateLimitPolicy, CacheConfig]]
] = {
    "safe": _safe_preset,
    "balanced": _balanced_preset,
    "throughput": _throughput_preset,
}

#: Valid preset names, in declaration order (safe -> balanced -> throughput).
GATEWAY_PRESET_NAMES: tuple[str, ...] = tuple(_PRESET_FACTORIES)


@dataclass(frozen=True)
class GatewayPreset:
    """A named bundle of gateway authz, retry, quota, and cache config (#664).

    Use :meth:`from_preset` to get a named starting point, then override
    individual blocks (``policy`` / ``retry`` / ``rate_limits`` / ``cache``)
    wholesale via an explicit config block or ``ProxyRuntime`` argument —
    presets only fill blocks that are not explicitly supplied.

    Attributes:
        name: The preset name (one of :data:`GATEWAY_PRESET_NAMES`).
        schema: Always :data:`GATEWAY_PRESET_SCHEMA`.
        policy: Runtime authorization gate (issue #373).
        retry: Upstream dispatch retry policy (issue #529).
        rate_limits: Per-session invocation quotas (issue #482).
        cache: Read-only response cache config (issue #512).
    """

    name: str
    schema: str
    policy: ToolPolicy
    retry: RetryPolicy
    rate_limits: RateLimitPolicy
    cache: CacheConfig

    @classmethod
    def from_preset(cls, name: str) -> GatewayPreset:
        """Construct a :class:`GatewayPreset` from a named preset.

        Supported presets:

        * ``"safe"`` — every ``tool_execute`` call requires approval; low
          per-minute quota; caching off.
        * ``"balanced"`` — allow-all authz; moderate quota; caching off.
        * ``"throughput"`` — allow-all authz; no quota; generous retries;
          read-only caching on (see the warning on :class:`CacheConfig`).

        Args:
            name: One of :data:`GATEWAY_PRESET_NAMES`.

        Returns:
            A fully populated :class:`GatewayPreset` with fresh, unshared
            config objects.

        Raises:
            ConfigError: If *name* is not a recognised preset.
        """
        factory = _PRESET_FACTORIES.get(name)
        if factory is None:
            valid = ", ".join(f'"{k}"' for k in sorted(_PRESET_FACTORIES))
            raise ConfigError(f"Unknown gateway policy preset {name!r}. Valid presets: {valid}.")
        policy, retry, rate_limits, cache = factory()
        return cls(
            name=name,
            schema=GATEWAY_PRESET_SCHEMA,
            policy=policy,
            retry=retry,
            rate_limits=rate_limits,
            cache=cache,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a deterministic, JSON-compatible dict.

        Used by ``mcp serve --print-effective-policy`` to export the
        resolved (preset-or-overridden) policy for audit.
        """
        return {
            "schema": self.schema,
            "name": self.name,
            "policy": self.policy.to_dict(),
            "retry": self.retry.to_dict(),
            "rate_limits": self.rate_limits.to_dict(),
            "cache": self.cache.to_dict(),
        }

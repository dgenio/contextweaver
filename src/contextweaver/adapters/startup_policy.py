"""Fault-tolerant multi-upstream startup policy and status reporting (#374).

Pairs with :mod:`contextweaver.adapters.upstream_config` (the per-upstream
spec) and :mod:`contextweaver.adapters.upstream_launch` (the behaviour that
produces a :class:`StartupReport`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from contextweaver.adapters._config_coerce import coerce_bool
from contextweaver.exceptions import ConfigError

#: Supported startup fault-tolerance modes.
STARTUP_MODES: frozenset[str] = frozenset({"degraded", "strict"})

_STARTUP_KEYS: frozenset[str] = frozenset(
    {"mode", "upstream_timeout_seconds", "min_healthy_upstreams", "fail_on_empty_catalog"}
)


@dataclass(frozen=True)
class StartupPolicy:
    """Fault-tolerant multi-upstream startup policy (#374).

    Attributes:
        mode: ``"degraded"`` (default) starts with whatever upstreams come up
            healthy, as long as :attr:`min_healthy_upstreams` is met and no
            *required* upstream failed. ``"strict"`` aborts startup entirely
            if any required upstream fails.
        upstream_timeout_seconds: Per-upstream connect + initial ``tools/list``
            timeout.
        min_healthy_upstreams: Minimum number of upstreams that must start
            successfully, in either mode.
        fail_on_empty_catalog: Whether an effective catalog of zero tools
            (e.g. every upstream's filters excluded everything) aborts
            startup.
    """

    mode: str = "degraded"
    upstream_timeout_seconds: float = 10.0
    min_healthy_upstreams: int = 1
    fail_on_empty_catalog: bool = True

    def __post_init__(self) -> None:
        if self.mode not in STARTUP_MODES:
            allowed = ", ".join(sorted(STARTUP_MODES))
            raise ConfigError(f"startup.mode must be one of {allowed}, got {self.mode!r}")
        if self.upstream_timeout_seconds <= 0:
            raise ConfigError("startup.upstream_timeout_seconds must be positive")
        if self.min_healthy_upstreams < 0:
            raise ConfigError("startup.min_healthy_upstreams must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "mode": self.mode,
            "upstream_timeout_seconds": self.upstream_timeout_seconds,
            "min_healthy_upstreams": self.min_healthy_upstreams,
            "fail_on_empty_catalog": self.fail_on_empty_catalog,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StartupPolicy:
        """Build from the ``startup`` config block."""
        if not isinstance(data, dict):
            raise ConfigError("startup config must be a mapping")
        unknown = sorted(set(data) - _STARTUP_KEYS)
        if unknown:
            allowed = ", ".join(sorted(_STARTUP_KEYS))
            raise ConfigError(f"startup: unknown key(s) {unknown}; allowed: {allowed}")
        return cls(
            mode=str(data.get("mode", "degraded")),
            upstream_timeout_seconds=float(data.get("upstream_timeout_seconds", 10.0)),
            min_healthy_upstreams=int(data.get("min_healthy_upstreams", 1)),
            fail_on_empty_catalog=coerce_bool(
                "startup.fail_on_empty_catalog", data.get("fail_on_empty_catalog"), True
            ),
        )


@dataclass(frozen=True)
class UpstreamStatus:
    """Startup outcome for one configured upstream.

    Attributes:
        name: The upstream's config-block key.
        status: ``"loaded"``, ``"failed"``, ``"timed_out"``, or ``"skipped"``.
        tool_count: Number of tools exposed after include/exclude filtering
            (``0`` unless ``status == "loaded"``).
        error: Redacted, human-readable failure detail; ``None`` when loaded.
    """

    name: str
    status: str
    tool_count: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "name": self.name,
            "status": self.status,
            "tool_count": self.tool_count,
            "error": self.error,
        }


@dataclass(frozen=True)
class StartupReport:
    """Aggregate startup outcome across all configured upstreams.

    Attributes:
        statuses: Per-upstream :class:`UpstreamStatus`, in configured order.
        collisions: Deterministic diagnostics for tool names claimed by more
            than one upstream after namespacing (see
            :func:`detect_tool_name_collisions`); empty when there are none.
    """

    statuses: tuple[UpstreamStatus, ...]
    collisions: tuple[str, ...] = ()

    @property
    def healthy_count(self) -> int:
        """Number of upstreams that started successfully."""
        return sum(1 for s in self.statuses if s.status == "loaded")

    @property
    def total_tools(self) -> int:
        """Total tool count across every successfully-started upstream."""
        return sum(s.tool_count for s in self.statuses)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "statuses": [s.to_dict() for s in self.statuses],
            "collisions": list(self.collisions),
        }

    def render_lines(self) -> list[str]:
        """Render one human-readable status line per upstream, for stderr logging."""
        lines = []
        for s in self.statuses:
            detail = f" tools={s.tool_count}" if s.status == "loaded" else f" error={s.error}"
            lines.append(f"upstream {s.name!r}: {s.status}{detail}")
        for collision in self.collisions:
            lines.append(f"collision: {collision}")
        return lines


def detect_tool_name_collisions(per_upstream_names: dict[str, list[str]]) -> list[str]:
    """Return deterministic collision diagnostics across upstream tool-name lists.

    Args:
        per_upstream_names: Maps upstream name -> the (already namespaced)
            tool names it advertises, in the *declared* upstream order (dict
            insertion order) — the same order passed to
            :class:`~contextweaver.adapters.mcp_upstream.MultiplexUpstream`,
            so the reported winner matches its real first-registered-wins
            behaviour.

    Returns:
        One message per tool name claimed by more than one upstream. The
        returned list is sorted by tool name for stable diagnostics output;
        within each message, the claiming upstreams are listed in
        declaration order and the first one is the actual routing winner.
    """
    owners: dict[str, list[str]] = {}
    for upstream_name, tool_names in per_upstream_names.items():
        for tool_name in tool_names:
            owners.setdefault(tool_name, []).append(upstream_name)
    return [
        f"tool {tool_name!r} claimed by upstreams {owners[tool_name]!r}; "
        f"{owners[tool_name][0]!r} wins (first-registered)"
        for tool_name in sorted(owners)
        if len(owners[tool_name]) > 1
    ]


__all__ = [
    "STARTUP_MODES",
    "StartupPolicy",
    "StartupReport",
    "UpstreamStatus",
    "detect_tool_name_collisions",
]

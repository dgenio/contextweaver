"""Live catalog refresh on upstream ``tools/list_changed`` notifications (#424).

When a live upstream MCP server declares the ``listChanged`` tools capability
it may emit ``notifications/tools/list_changed`` at any time.  This module
turns those notifications into calls to the runtime's existing *atomic*
refresh path (``ProxyRuntime.register_tool_defs_sync``, issue #507) so the
gateway catalog, graph, router, validators, and result cache are all rebuilt
consistently — without polling and without restarting the server.

Wiring (the coordinator owns ``_mcp_cli``):

1. Build a :class:`LiveRefresher` with the serving :class:`ProxyRuntime`
   (anything satisfying :class:`CatalogRefreshRuntime`), an async
   ``list_tools`` callable (typically a lazily-bound closure over the
   :class:`~contextweaver.adapters.mcp_upstream.MultiplexUpstream` returned by
   ``launch_upstreams``), and a :class:`LiveRefreshPolicy` with ``enabled=True``.
2. Pass ``message_handler=make_message_handler(refresher)`` to
   :func:`~contextweaver.adapters.upstream_launch.launch_upstreams` so every
   upstream session routes its server notifications through the refresher.

The default policy is inert (``enabled=False``): an unconfigured gateway
behaves exactly as before.  Refreshes are debounced (bursts of notifications
within :attr:`LiveRefreshPolicy.debounce_seconds` of the last refresh are
collapsed) and rate-limited over a sliding 60-second window.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol, runtime_checkable

from mcp import types as mcp_types
from mcp.shared.session import RequestResponder

from contextweaver.diagnostics import DiagnosticEvent, DiagnosticSink
from contextweaver.exceptions import ConfigError

logger = logging.getLogger("contextweaver.adapters.live_refresh")

#: A monotonic clock returning seconds (mirrors ``gateway_controls.Clock``).
Clock = Callable[[], float]

#: Async source of MCP-format tool definitions (the upstream ``tools/list``).
ListToolsFn = Callable[[], Awaitable[list[dict[str, Any]]]]

#: The MCP SDK ``ClientSession(message_handler=...)`` callback shape
#: (``mcp.client.session.MessageHandlerFnT``), spelled out so callers can
#: type against it without importing the SDK's private-ish protocol name.
MessageHandler = Callable[
    [
        RequestResponder[mcp_types.ServerRequest, mcp_types.ClientResult]
        | mcp_types.ServerNotification
        | Exception
    ],
    Awaitable[None],
]

#: Sliding rate-limit window length in seconds.
_WINDOW_SECONDS = 60.0

#: Stable diagnostic event name for live refresh outcomes.
LIVE_REFRESH_EVENT = "catalog.refresh.live"


@runtime_checkable
class CatalogRefreshRuntime(Protocol):
    """The slice of :class:`~contextweaver.adapters.proxy_runtime.ProxyRuntime` this module needs.

    ``register_tool_defs_sync`` is the runtime's atomic catalog-refresh entry
    point (issue #507): all derived state (catalog, graph, router, validator
    cache, result cache) is rebuilt within the single synchronous call, so
    concurrent executions never observe a half-updated view.
    """

    def register_tool_defs_sync(self, tool_defs: list[dict[str, Any]]) -> int:
        """Register MCP-format *tool_defs* atomically; return the count."""
        ...


@dataclass(frozen=True)
class LiveRefreshPolicy:
    """Config for notification-driven catalog refresh (issue #424).

    The default is inert: ``enabled=False`` means notifications are ignored
    and the gateway behaves exactly as before.

    Attributes:
        enabled: Whether ``tools/list_changed`` notifications trigger a
            catalog refresh at all.
        debounce_seconds: Notifications arriving within this many seconds of
            the last completed refresh are collapsed (no refresh runs).  The
            next notification after the window refreshes and picks up every
            intervening change, since the tool list is re-fetched at that
            moment.
        max_refreshes_per_minute: Upper bound on refreshes in any sliding
            60-second window, protecting the router/graph rebuild from a
            notification storm.
    """

    enabled: bool = False
    debounce_seconds: float = 2.0
    max_refreshes_per_minute: int = 6

    def __post_init__(self) -> None:
        if self.debounce_seconds < 0:
            raise ConfigError("LiveRefreshPolicy.debounce_seconds must be non-negative")
        if self.max_refreshes_per_minute < 1:
            raise ConfigError("LiveRefreshPolicy.max_refreshes_per_minute must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "enabled": self.enabled,
            "debounce_seconds": self.debounce_seconds,
            "max_refreshes_per_minute": self.max_refreshes_per_minute,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LiveRefreshPolicy:
        """Build from a config mapping (e.g. a ``mcp serve`` ``live_refresh`` block).

        Raises:
            ConfigError: If *data* is not a mapping or a field has the wrong type.
        """
        if not isinstance(data, dict):
            raise ConfigError("live_refresh config must be a mapping")
        enabled = data.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ConfigError("live_refresh.enabled must be a boolean")
        debounce = data.get("debounce_seconds", 2.0)
        if isinstance(debounce, bool) or not isinstance(debounce, (int, float)):
            raise ConfigError("live_refresh.debounce_seconds must be a number")
        per_minute = data.get("max_refreshes_per_minute", 6)
        if isinstance(per_minute, bool) or not isinstance(per_minute, int):
            raise ConfigError("live_refresh.max_refreshes_per_minute must be an integer")
        return cls(
            enabled=enabled,
            debounce_seconds=float(debounce),
            max_refreshes_per_minute=per_minute,
        )


class LiveRefresher:
    """Debounced, rate-limited bridge from notifications to catalog refresh.

    Args:
        runtime: The serving runtime; only
            :meth:`CatalogRefreshRuntime.register_tool_defs_sync` is used.
        list_tools: Async callable returning the current MCP-format tool
            definitions (the aggregated upstream ``tools/list``).
        policy: Refresh policy.  Defaults to the inert
            :class:`LiveRefreshPolicy` (``enabled=False``).
        clock: Injectable monotonic clock for debounce / rate-limit windows.
        diagnostic_sink: Optional sink receiving one sanitized
            :data:`LIVE_REFRESH_EVENT` event per handled notification.
        session_id: Optional identifier stamped on diagnostic events.
    """

    def __init__(
        self,
        runtime: CatalogRefreshRuntime,
        list_tools: ListToolsFn,
        *,
        policy: LiveRefreshPolicy | None = None,
        clock: Clock = time.monotonic,
        diagnostic_sink: DiagnosticSink | None = None,
        session_id: str = "",
    ) -> None:
        self._runtime = runtime
        self._list_tools = list_tools
        self._policy = policy or LiveRefreshPolicy()
        self._clock = clock
        self._sink = diagnostic_sink
        self._session_id = session_id
        self._lock = asyncio.Lock()
        #: Monotonic time of the last completed refresh (debounce anchor).
        self._last_refresh: float | None = None
        #: Completed-refresh timestamps inside the sliding rate-limit window.
        self._window: deque[float] = deque()

    @property
    def policy(self) -> LiveRefreshPolicy:
        """Return the active :class:`LiveRefreshPolicy`."""
        return self._policy

    async def on_list_changed(self) -> bool:
        """Handle one ``tools/list_changed`` notification.

        Applies the debounce window and the sliding-minute rate limit, then
        re-fetches the upstream tool list and refreshes the runtime catalog
        atomically.  Fetch or refresh failures are logged and reported to the
        diagnostic sink — never raised, since this runs inside the MCP client
        session's receive loop.

        Returns:
            ``True`` if a refresh ran, ``False`` otherwise (disabled policy,
            debounced, rate-limited, or failed).
        """
        if not self._policy.enabled:
            return False
        async with self._lock:
            started = perf_counter()
            now = self._clock()
            if (
                self._last_refresh is not None
                and now - self._last_refresh < self._policy.debounce_seconds
            ):
                self._emit("debounced", started)
                return False
            while self._window and now - self._window[0] >= _WINDOW_SECONDS:
                self._window.popleft()
            if len(self._window) >= self._policy.max_refreshes_per_minute:
                self._emit("rate_limited", started)
                return False
            try:
                tool_defs = await self._list_tools()
                registered = self._runtime.register_tool_defs_sync(tool_defs)
            except Exception as exc:
                logger.warning("live_refresh: catalog refresh failed: %r", exc)
                self._emit("error", started, success=False)
                return False
            self._last_refresh = now
            self._window.append(now)
            self._emit("refreshed", started, registered=registered)
            logger.debug("live_refresh: refreshed catalog (%d tools)", registered)
            return True

    def _emit(
        self,
        outcome: str,
        started: float,
        *,
        success: bool = True,
        registered: int | None = None,
    ) -> None:
        """Send one metadata-only diagnostic event to the sink, if configured."""
        if self._sink is None:
            return
        attributes: dict[str, Any] = {"outcome": outcome}
        if registered is not None:
            attributes["registered"] = registered
        self._sink.emit(
            DiagnosticEvent(
                event=LIVE_REFRESH_EVENT,
                success=success,
                duration_ms=(perf_counter() - started) * 1000,
                session_id=self._session_id,
                attributes=attributes,
            )
        )


def make_message_handler(refresher: LiveRefresher) -> MessageHandler:
    """Return a ``ClientSession`` message handler that drives *refresher*.

    The MCP SDK exposes incoming server traffic to clients through the
    ``ClientSession(..., message_handler=...)`` callback (there is no
    typed per-notification hook).  The returned handler triggers
    :meth:`LiveRefresher.on_list_changed` for
    :class:`mcp.types.ToolListChangedNotification` and ignores every other
    request/notification/exception, leaving the session's default handling
    untouched.

    Args:
        refresher: The refresher to notify.

    Returns:
        A callback suitable for ``ClientSession(message_handler=...)`` — and
        therefore for
        :func:`~contextweaver.adapters.upstream_launch.launch_upstreams`'s
        ``message_handler`` keyword.
    """

    async def handler(
        message: RequestResponder[mcp_types.ServerRequest, mcp_types.ClientResult]
        | mcp_types.ServerNotification
        | Exception,
    ) -> None:
        if isinstance(message, mcp_types.ServerNotification) and isinstance(
            message.root, mcp_types.ToolListChangedNotification
        ):
            await refresher.on_list_changed()

    return handler


__all__ = [
    "LIVE_REFRESH_EVENT",
    "CatalogRefreshRuntime",
    "LiveRefreshPolicy",
    "LiveRefresher",
    "MessageHandler",
    "make_message_handler",
]

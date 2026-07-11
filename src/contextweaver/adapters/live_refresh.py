"""Live catalog refresh on upstream ``tools/list_changed`` notifications (#424).

When a live upstream declares the ``listChanged`` tools capability it may emit
``notifications/tools/list_changed`` at any time.  This module turns those into
calls to the runtime's *atomic* refresh path
(``ProxyRuntime.register_tool_defs_sync``, issue #507) so the gateway catalog,
graph, router, validators, and result cache rebuild consistently — without
polling or restarting.

Wiring (the coordinator owns ``_mcp_cli``): build a :class:`LiveRefresher` with
the serving :class:`ProxyRuntime` (any :class:`CatalogRefreshRuntime`), an async
``list_tools`` callable (typically a closure over the ``launch_upstreams``
:class:`~contextweaver.adapters.mcp_upstream.MultiplexUpstream`), and a
:class:`LiveRefreshPolicy` with ``enabled=True``; then pass
``message_handler=make_message_handler(refresher)`` to
:func:`~contextweaver.adapters.upstream_launch.launch_upstreams`.

The default policy is inert (``enabled=False``): an unconfigured gateway
behaves exactly as before.  Refreshes are debounced (bursts within
:attr:`LiveRefreshPolicy.debounce_seconds` are collapsed) and rate-limited over
a sliding 60-second window.
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
        debounce_seconds: Notifications within this many seconds of the last
            completed refresh are collapsed; the next one after the window
            re-fetches and picks up every intervening change.
        max_refreshes_per_minute: Upper bound on refreshes in any sliding
            60-second window, protecting the router/graph rebuild from a storm.
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
        runtime: Serving runtime; only
            :meth:`CatalogRefreshRuntime.register_tool_defs_sync` is used.
        list_tools: Async callable returning the aggregated upstream
            ``tools/list`` (MCP-format tool definitions).
        policy: Refresh policy; defaults to the inert
            :class:`LiveRefreshPolicy` (``enabled=False``).
        clock: Injectable monotonic clock for debounce / rate-limit windows.
        diagnostic_sink: Optional sink receiving one sanitized
            :data:`LIVE_REFRESH_EVENT` per handled notification.
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
        self._last_refresh: float | None = None  # last completed refresh (debounce anchor)
        self._window: deque[float] = deque()  # completed-refresh times in the rate window
        # In-flight refresh tasks offloaded from the receive loop (#424), retained
        # so they are not GC'd mid-flight and can be drained/awaited (wait_idle).
        self._tasks: set[asyncio.Task[bool]] = set()

    @property
    def policy(self) -> LiveRefreshPolicy:
        """Return the active :class:`LiveRefreshPolicy`."""
        return self._policy

    async def on_list_changed(self) -> bool:
        """Handle one ``tools/list_changed`` notification.

        Applies debounce + sliding-minute rate limit, then re-fetches the
        upstream tool list and refreshes the catalog atomically. Failures are
        logged/reported to the diagnostic sink, never raised. Returns ``True``
        if a refresh ran, else ``False`` (disabled, debounced, rate-limited,
        or failed).
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

    def schedule_on_list_changed(self) -> asyncio.Task[bool]:
        """Schedule :meth:`on_list_changed` off the receive loop and return the task.

        Awaiting it in the ``ClientSession`` receive loop would deadlock (the
        refresh's ``tools/list`` needs that loop free, #424); await via :meth:`wait_idle`.
        """
        task = asyncio.ensure_future(self.on_list_changed())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def wait_idle(self) -> None:
        """Await all in-flight refresh tasks (shutdown drain / tests); safe when none."""
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

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

    The SDK surfaces incoming server traffic through the
    ``ClientSession(..., message_handler=...)`` callback (no typed
    per-notification hook).  For a :class:`mcp.types.ToolListChangedNotification`
    the handler *schedules* the refresh via
    :meth:`LiveRefresher.schedule_on_list_changed` — it never awaits it, so the
    session receive loop stays free (issue #424) — and ignores every other
    request/notification/exception.  Suitable for
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
            refresher.schedule_on_list_changed()  # offload; never await in the loop

    return handler


__all__ = [
    "LIVE_REFRESH_EVENT",
    "CatalogRefreshRuntime",
    "LiveRefreshPolicy",
    "LiveRefresher",
    "MessageHandler",
    "make_message_handler",
]

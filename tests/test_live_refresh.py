"""Tests for notification-driven live catalog refresh (issue #424)."""

from __future__ import annotations

from typing import Any

import pytest
from mcp import types as mcp_types

from contextweaver.adapters.live_refresh import (
    LIVE_REFRESH_EVENT,
    LiveRefresher,
    LiveRefreshPolicy,
    make_message_handler,
)
from contextweaver.diagnostics import InMemoryDiagnosticSink
from contextweaver.exceptions import ConfigError

_TOOL_DEFS = [{"name": "alpha", "description": "first tool", "inputSchema": {"type": "object"}}]


class _FakeRuntime:
    """Records atomic-refresh calls (satisfies CatalogRefreshRuntime)."""

    def __init__(self) -> None:
        self.calls: list[list[dict[str, Any]]] = []

    def register_tool_defs_sync(self, tool_defs: list[dict[str, Any]]) -> int:
        self.calls.append(tool_defs)
        return len(tool_defs)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _refresher(
    runtime: _FakeRuntime,
    clock: _Clock,
    *,
    policy: LiveRefreshPolicy | None = None,
    sink: InMemoryDiagnosticSink | None = None,
) -> LiveRefresher:
    async def list_tools() -> list[dict[str, Any]]:
        return list(_TOOL_DEFS)

    return LiveRefresher(
        runtime,
        list_tools,
        policy=policy or LiveRefreshPolicy(enabled=True),
        clock=clock,
        diagnostic_sink=sink,
        session_id="s1",
    )


async def test_disabled_policy_never_refreshes() -> None:
    runtime, clock = _FakeRuntime(), _Clock()
    refresher = _refresher(runtime, clock, policy=LiveRefreshPolicy())
    assert await refresher.on_list_changed() is False
    assert runtime.calls == []


async def test_refresh_runs_and_registers_tools() -> None:
    runtime, clock = _FakeRuntime(), _Clock()
    refresher = _refresher(runtime, clock)
    assert await refresher.on_list_changed() is True
    assert runtime.calls == [_TOOL_DEFS]


async def test_debounce_collapses_bursts() -> None:
    runtime, clock = _FakeRuntime(), _Clock()
    refresher = _refresher(
        runtime, clock, policy=LiveRefreshPolicy(enabled=True, debounce_seconds=2.0)
    )
    assert await refresher.on_list_changed() is True
    clock.now += 0.5
    assert await refresher.on_list_changed() is False  # inside debounce window
    clock.now += 2.0
    assert await refresher.on_list_changed() is True  # window elapsed
    assert len(runtime.calls) == 2


async def test_rate_limit_enforced_over_sliding_minute() -> None:
    runtime, clock = _FakeRuntime(), _Clock()
    policy = LiveRefreshPolicy(enabled=True, debounce_seconds=0.0, max_refreshes_per_minute=2)
    refresher = _refresher(runtime, clock, policy=policy)
    assert await refresher.on_list_changed() is True
    clock.now += 1
    assert await refresher.on_list_changed() is True
    clock.now += 1
    assert await refresher.on_list_changed() is False  # third within 60s window
    clock.now += 60
    assert await refresher.on_list_changed() is True  # window slid
    assert len(runtime.calls) == 3


async def test_fetch_failure_is_swallowed_and_reported() -> None:
    runtime, clock = _FakeRuntime(), _Clock()
    sink = InMemoryDiagnosticSink()

    async def failing_list_tools() -> list[dict[str, Any]]:
        raise RuntimeError("upstream gone")

    refresher = LiveRefresher(
        runtime,
        failing_list_tools,
        policy=LiveRefreshPolicy(enabled=True),
        clock=clock,
        diagnostic_sink=sink,
    )
    assert await refresher.on_list_changed() is False
    assert runtime.calls == []
    events = [e for e in sink.events() if e.event == LIVE_REFRESH_EVENT]
    assert events and events[-1].success is False
    assert events[-1].attributes["outcome"] == "error"


async def test_diagnostic_event_on_success() -> None:
    runtime, clock = _FakeRuntime(), _Clock()
    sink = InMemoryDiagnosticSink()
    refresher = _refresher(runtime, clock, sink=sink)
    await refresher.on_list_changed()
    (event,) = [e for e in sink.events() if e.event == LIVE_REFRESH_EVENT]
    assert event.success is True
    assert event.attributes == {"outcome": "refreshed", "registered": 1}
    assert event.session_id == "s1"


async def test_message_handler_triggers_only_on_tool_list_changed() -> None:
    runtime, clock = _FakeRuntime(), _Clock()
    refresher = _refresher(runtime, clock)
    handler = make_message_handler(refresher)

    notification = mcp_types.ServerNotification(
        mcp_types.ToolListChangedNotification(method="notifications/tools/list_changed")
    )
    await handler(notification)
    assert len(runtime.calls) == 1

    other = mcp_types.ServerNotification(
        mcp_types.ResourceListChangedNotification(method="notifications/resources/list_changed")
    )
    await handler(other)
    await handler(RuntimeError("transport hiccup"))
    assert len(runtime.calls) == 1  # unchanged


def test_policy_validation_and_serde() -> None:
    policy = LiveRefreshPolicy(enabled=True, debounce_seconds=1.5, max_refreshes_per_minute=3)
    assert LiveRefreshPolicy.from_dict(policy.to_dict()) == policy
    with pytest.raises(ConfigError):
        LiveRefreshPolicy(debounce_seconds=-1)
    with pytest.raises(ConfigError):
        LiveRefreshPolicy(max_refreshes_per_minute=0)
    with pytest.raises(ConfigError):
        LiveRefreshPolicy.from_dict({"enabled": "yes"})
    with pytest.raises(ConfigError):
        LiveRefreshPolicy.from_dict({"debounce_seconds": "fast"})

"""Tests for contextweaver.adapters.upstream_launch (issues #366/#368/#374).

The real ``stdio``/``http``/``sse`` connectors spawn a child process or open
a network connection, so these tests monkeypatch ``_CONNECTORS`` with fakes
that hand back an in-memory session — the same technique the MCP SDK's own
in-memory transport uses, and the transport-specific connector functions
themselves are covered by ``mypy --strict`` against the real SDK signatures.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import Any

import pytest

from contextweaver.adapters import upstream_launch
from contextweaver.adapters.mcp_upstream import StubUpstream
from contextweaver.adapters.startup_policy import StartupPolicy
from contextweaver.adapters.upstream_config import UpstreamSpec
from contextweaver.adapters.upstream_launch import NamespacedFilteredUpstream, launch_upstreams
from contextweaver.exceptions import UpstreamStartupError


class _FakeSession:
    """Stands in for a connected ``mcp.ClientSession`` at the ``list_tools`` boundary."""

    def __init__(
        self,
        tools: list[dict[str, Any]],
        *,
        error: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self._tools = tools
        self._error = error
        self._delay = delay

    async def list_tools(self) -> list[dict[str, Any]]:
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return list(self._tools)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": f"called {name}"}], "isError": False}


def _fake_connector(sessions: dict[str, _FakeSession]) -> Any:  # noqa: ANN401 - test double
    async def _connect(spec: UpstreamSpec, stack: AsyncExitStack, timeout: float) -> _FakeSession:
        return sessions[spec.name]

    return _connect


# ---------------------------------------------------------------------------
# NamespacedFilteredUpstream
# ---------------------------------------------------------------------------


async def test_filtered_upstream_prefixes_namespace() -> None:
    inner = StubUpstream([{"name": "read_file", "description": "x", "inputSchema": {}}])
    wrapped = NamespacedFilteredUpstream(inner, namespace="fs")
    tools = await wrapped.list_tools()
    assert tools[0]["name"] == "fs.read_file"


async def test_filtered_upstream_no_namespace_passes_through() -> None:
    inner = StubUpstream([{"name": "read_file", "description": "x", "inputSchema": {}}])
    wrapped = NamespacedFilteredUpstream(inner)
    tools = await wrapped.list_tools()
    assert tools[0]["name"] == "read_file"


async def test_filtered_upstream_call_tool_strips_namespace() -> None:
    seen: dict[str, str] = {}

    async def handler(name: str, args: dict[str, Any]) -> dict[str, Any]:
        seen["name"] = name
        return {"content": [{"type": "text", "text": "ok"}], "isError": False}

    inner = StubUpstream(
        [{"name": "read_file", "description": "x", "inputSchema": {}}], handler=handler
    )
    wrapped = NamespacedFilteredUpstream(inner, namespace="fs")
    await wrapped.list_tools()
    result = await wrapped.call_tool("fs.read_file", {})
    assert seen["name"] == "read_file"
    assert result["isError"] is False


async def test_filtered_upstream_applies_include_exclude() -> None:
    inner = StubUpstream(
        [
            {"name": "read_file", "description": "x", "inputSchema": {}},
            {"name": "delete_file", "description": "x", "inputSchema": {}},
        ]
    )
    wrapped = NamespacedFilteredUpstream(
        inner, include_tools=("read_*",), exclude_tools=("delete_*",)
    )
    tools = await wrapped.list_tools()
    assert [t["name"] for t in tools] == ["read_file"]


# ---------------------------------------------------------------------------
# launch_upstreams
# ---------------------------------------------------------------------------


async def test_degraded_mode_tolerates_optional_upstream_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = {
        "good": _FakeSession([{"name": "read_file", "description": "x", "inputSchema": {}}]),
        "bad": _FakeSession([], error=ConnectionError("refused")),
    }
    monkeypatch.setitem(upstream_launch._CONNECTORS, "stdio", _fake_connector(sessions))
    specs = [
        UpstreamSpec(name="good", type="stdio", command="echo"),
        UpstreamSpec(name="bad", type="stdio", command="echo", required=False),
    ]
    async with AsyncExitStack() as stack:
        multiplex, report = await launch_upstreams(specs, StartupPolicy(), stack)
        tools = await multiplex.list_tools()
    assert report.healthy_count == 1
    assert [t["name"] for t in tools] == ["read_file"]
    bad_status = next(s for s in report.statuses if s.name == "bad")
    assert bad_status.status == "failed"
    assert bad_status.error is not None


async def test_strict_mode_raises_on_required_upstream_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = {"bad": _FakeSession([], error=ConnectionError("refused"))}
    monkeypatch.setitem(upstream_launch._CONNECTORS, "stdio", _fake_connector(sessions))
    specs = [UpstreamSpec(name="bad", type="stdio", command="echo", required=True)]
    async with AsyncExitStack() as stack:
        with pytest.raises(UpstreamStartupError, match="required upstream"):
            await launch_upstreams(specs, StartupPolicy(mode="strict"), stack)


async def test_degraded_mode_still_enforces_min_healthy_upstreams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = {"bad": _FakeSession([], error=ConnectionError("refused"))}
    monkeypatch.setitem(upstream_launch._CONNECTORS, "stdio", _fake_connector(sessions))
    specs = [UpstreamSpec(name="bad", type="stdio", command="echo", required=False)]
    policy = StartupPolicy(mode="degraded", min_healthy_upstreams=1)
    async with AsyncExitStack() as stack:
        with pytest.raises(UpstreamStartupError, match="only 0 upstream"):
            await launch_upstreams(specs, policy, stack)


async def test_connect_timeout_is_recorded_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = {
        "slow": _FakeSession([{"name": "x", "description": "x", "inputSchema": {}}], delay=0.2)
    }
    monkeypatch.setitem(upstream_launch._CONNECTORS, "stdio", _fake_connector(sessions))
    specs = [UpstreamSpec(name="slow", type="stdio", command="echo", required=False)]
    policy = StartupPolicy(
        upstream_timeout_seconds=0.01, min_healthy_upstreams=0, fail_on_empty_catalog=False
    )
    async with AsyncExitStack() as stack:
        _multiplex, report = await launch_upstreams(specs, policy, stack)
    assert report.statuses[0].status == "timed_out"


async def test_empty_effective_catalog_raises_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = {"fs": _FakeSession([{"name": "read_file", "description": "x", "inputSchema": {}}])}
    monkeypatch.setitem(upstream_launch._CONNECTORS, "stdio", _fake_connector(sessions))
    specs = [UpstreamSpec(name="fs", type="stdio", command="echo", exclude_tools=("*",))]
    async with AsyncExitStack() as stack:
        with pytest.raises(UpstreamStartupError, match="empty"):
            await launch_upstreams(specs, StartupPolicy(), stack)


async def test_collisions_reported_in_declaration_order(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = {
        "a": _FakeSession([{"name": "shared", "description": "x", "inputSchema": {}}]),
        "b": _FakeSession([{"name": "shared", "description": "x", "inputSchema": {}}]),
    }
    monkeypatch.setitem(upstream_launch._CONNECTORS, "stdio", _fake_connector(sessions))
    specs = [
        UpstreamSpec(name="a", type="stdio", command="echo"),
        UpstreamSpec(name="b", type="stdio", command="echo"),
    ]
    async with AsyncExitStack() as stack:
        _multiplex, report = await launch_upstreams(specs, StartupPolicy(), stack)
    assert len(report.collisions) == 1
    assert "'a' wins" in report.collisions[0]


async def test_call_tool_routes_to_owning_upstream_and_strips_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = {"fs": _FakeSession([{"name": "read_file", "description": "x", "inputSchema": {}}])}
    monkeypatch.setitem(upstream_launch._CONNECTORS, "stdio", _fake_connector(sessions))
    specs = [UpstreamSpec(name="fs", type="stdio", command="echo", namespace="fs")]
    async with AsyncExitStack() as stack:
        multiplex, _report = await launch_upstreams(specs, StartupPolicy(), stack)
        # Mirrors real usage: ProxyRuntime.refresh_catalog() always calls
        # list_tools() (building the owner index) before any tool_execute.
        await multiplex.list_tools()
        result = await multiplex.call_tool("fs.read_file", {})
    assert result["isError"] is False
    assert result["content"][0]["text"] == "called read_file"

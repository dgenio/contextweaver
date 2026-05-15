"""Tests for the concrete UpstreamCall implementations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from contextweaver.adapters.mcp_upstream import (
    McpClientUpstream,
    MultiplexUpstream,
    StubUpstream,
)

# ---------------------------------------------------------------------------
# StubUpstream
# ---------------------------------------------------------------------------


async def test_stub_upstream_list_tools_returns_defs() -> None:
    defs = [{"name": "a", "description": "x", "inputSchema": {"type": "object"}}]
    stub = StubUpstream(defs)
    out = await stub.list_tools()
    assert out == defs
    # Defensive copy — mutating the result must not affect future calls.
    out[0]["description"] = "mutated"
    assert (await stub.list_tools())[0]["description"] == "x"


async def test_stub_upstream_default_handler_returns_error() -> None:
    stub = StubUpstream([])
    result = await stub.call_tool("anything", {"arg": 1})
    assert result["isError"] is True


async def test_stub_upstream_custom_handler_runs() -> None:
    async def handler(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": name}], "isError": False}

    stub = StubUpstream([], handler=handler)
    result = await stub.call_tool("my_tool", {})
    assert result["isError"] is False
    assert result["content"][0]["text"] == "my_tool"


# ---------------------------------------------------------------------------
# MultiplexUpstream
# ---------------------------------------------------------------------------


async def test_multiplex_lists_tools_from_all_sources() -> None:
    a = StubUpstream([{"name": "a", "description": "from-a", "inputSchema": {}}])
    b = StubUpstream([{"name": "b", "description": "from-b", "inputSchema": {}}])
    mux = MultiplexUpstream([a, b])
    listing = await mux.list_tools()
    assert {t["name"] for t in listing} == {"a", "b"}


async def test_multiplex_first_source_wins_on_collision() -> None:
    a = StubUpstream([{"name": "shared", "description": "from-a", "inputSchema": {}}])
    b = StubUpstream([{"name": "shared", "description": "from-b", "inputSchema": {}}])
    mux = MultiplexUpstream([a, b])
    listing = await mux.list_tools()
    assert len(listing) == 1
    assert listing[0]["description"] == "from-a"


async def test_multiplex_routes_call_to_owner() -> None:
    seen: dict[str, str] = {}

    def make_handler(label: str) -> Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]:
        async def handler(name: str, args: dict[str, Any]) -> dict[str, Any]:
            seen[name] = label
            return {"content": [{"type": "text", "text": label}], "isError": False}

        return handler

    a = StubUpstream(
        [{"name": "a_tool", "description": "x", "inputSchema": {}}],
        handler=make_handler("a"),
    )
    b = StubUpstream(
        [{"name": "b_tool", "description": "x", "inputSchema": {}}],
        handler=make_handler("b"),
    )
    mux = MultiplexUpstream([a, b])
    await mux.list_tools()  # populate ownership index
    await mux.call_tool("a_tool", {})
    await mux.call_tool("b_tool", {})
    assert seen == {"a_tool": "a", "b_tool": "b"}


async def test_multiplex_unknown_tool_returns_error() -> None:
    mux = MultiplexUpstream([StubUpstream([])])
    result = await mux.call_tool("ghost", {})
    assert result["isError"] is True


# ---------------------------------------------------------------------------
# McpClientUpstream — coerces SDK objects to MCP-format dicts
# ---------------------------------------------------------------------------


class _FakeTool:
    """A minimal stand-in for ``mcp.types.Tool`` to exercise the coercion path."""

    def __init__(self, name: str, description: str, inputSchema: dict[str, Any]) -> None:  # noqa: N803
        self.name = name
        self.description = description
        self.inputSchema = inputSchema


class _FakeListing:
    def __init__(self, tools: list[Any]) -> None:
        self.tools = tools


class _FakeResult:
    def __init__(self, content: list[dict[str, Any]], isError: bool) -> None:  # noqa: N803
        self.content = content
        self.isError = isError


class _FakeSession:
    def __init__(self, tools: list[Any], result: Any) -> None:  # noqa: ANN401
        self._tools = tools
        self._result = result
        self.last_call: tuple[str, dict[str, Any]] | None = None

    async def list_tools(self) -> Any:  # noqa: ANN401
        return _FakeListing(self._tools)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:  # noqa: ANN401
        self.last_call = (name, arguments)
        return self._result


async def test_mcp_client_upstream_list_tools_coerces() -> None:
    session = _FakeSession(
        [_FakeTool("a", "alpha", {"type": "object"})],
        _FakeResult([], False),
    )
    upstream = McpClientUpstream(session)
    out = await upstream.list_tools()
    assert out == [{"name": "a", "description": "alpha", "inputSchema": {"type": "object"}}]


async def test_mcp_client_upstream_call_tool_coerces() -> None:
    session = _FakeSession(
        [],
        _FakeResult([{"type": "text", "text": "ok"}], False),
    )
    upstream = McpClientUpstream(session)
    out = await upstream.call_tool("t", {"x": 1})
    assert out == {"content": [{"type": "text", "text": "ok"}], "isError": False}
    assert session.last_call == ("t", {"x": 1})


async def test_mcp_client_upstream_translates_exceptions() -> None:
    class _Boom:
        async def list_tools(self) -> Any:  # noqa: ANN401
            raise RuntimeError("network")

        async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:  # noqa: ANN401
            raise RuntimeError("network")

    upstream = McpClientUpstream(_Boom())
    out = await upstream.call_tool("t", {})
    assert out["isError"] is True
    assert "network" in out["content"][0]["text"]


async def test_mcp_client_upstream_call_tool_timeout() -> None:
    """A hung call_tool returns an isError result after the timeout."""
    import asyncio

    class _HangSession:
        async def list_tools(self) -> Any:  # noqa: ANN401
            await asyncio.sleep(9999)

        async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:  # noqa: ANN401
            await asyncio.sleep(9999)

    upstream = McpClientUpstream(_HangSession(), timeout=0.01)
    out = await upstream.call_tool("slow_tool", {})
    assert out["isError"] is True
    assert "timeout" in out["content"][0]["text"].lower()


async def test_mcp_client_upstream_list_tools_timeout() -> None:
    """A hung list_tools raises TimeoutError (callers handle it)."""
    import asyncio

    class _HangSession:
        async def list_tools(self) -> Any:  # noqa: ANN401
            await asyncio.sleep(9999)

        async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:  # noqa: ANN401
            await asyncio.sleep(9999)

    upstream = McpClientUpstream(_HangSession(), timeout=0.01)
    with pytest.raises(asyncio.TimeoutError):
        await upstream.list_tools()

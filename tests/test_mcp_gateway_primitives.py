"""Tests for the resource/prompt gateway meta-tools + server wiring (#669 / #670)."""

from __future__ import annotations

import json
from typing import Any

from contextweaver.adapters.gateway_primitives import PrimitiveGatewayRuntime
from contextweaver.adapters.mcp_gateway import GATEWAY_TOOL_NAMES
from contextweaver.adapters.mcp_gateway_primitives import (
    PRIMITIVE_TOOL_NAMES,
    PROMPT_BROWSE,
    PROMPT_GET,
    RESOURCE_BROWSE,
    RESOURCE_READ,
    dispatch_primitive_meta_tool,
    make_primitive_meta_tools,
)
from contextweaver.adapters.mcp_gateway_server import McpGatewayServer
from contextweaver.adapters.mcp_upstream import StubUpstream
from contextweaver.adapters.proxy_runtime import ProxyRuntime
from contextweaver.envelope import CHOICE_CARD_KINDS

RESOURCES = [
    {"uri": "file:///docs/readme.md", "name": "README", "mimeType": "text/markdown"},
    {"uri": "file:///docs/api.md", "name": "API guide", "mimeType": "text/markdown"},
]
PROMPTS = [
    {
        "name": "summarize",
        "description": "Summarize text",
        "arguments": [{"name": "text", "required": True}],
    },
]


class StubPrimitiveUpstream:
    async def list_resources(self) -> list[dict[str, Any]]:
        return [dict(r) for r in RESOURCES]

    async def read_resource(self, uri: str) -> dict[str, Any]:
        return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": f"body {uri}"}]}

    async def list_prompts(self) -> list[dict[str, Any]]:
        return [dict(p) for p in PROMPTS]

    async def get_prompt(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"messages": [{"role": "user", "content": {"type": "text", "text": "hi"}}]}


def _primitive_runtime() -> PrimitiveGatewayRuntime:
    rt = PrimitiveGatewayRuntime(StubPrimitiveUpstream())
    rt.register_sync(RESOURCES, PROMPTS)
    return rt


def test_kind_set_includes_resource_and_prompt() -> None:
    assert "resource" in CHOICE_CARD_KINDS
    assert "prompt" in CHOICE_CARD_KINDS


def test_make_primitive_meta_tools_returns_four() -> None:
    tools = make_primitive_meta_tools()
    assert [t["name"] for t in tools] == list(PRIMITIVE_TOOL_NAMES)
    # No banned schema fields leak into the meta-tool definitions.
    for tool in tools:
        assert set(tool) == {"name", "description", "inputSchema"}


async def test_dispatch_resource_browse_then_read() -> None:
    rt = _primitive_runtime()
    browse = await dispatch_primitive_meta_tool(rt, RESOURCE_BROWSE, {"query": "readme"})
    assert browse["isError"] is False
    cards = json.loads(browse["content"][0]["text"])
    assert cards and all(c["kind"] == "resource" for c in cards)
    read = await dispatch_primitive_meta_tool(rt, RESOURCE_READ, {"resource_id": cards[0]["id"]})
    assert read["isError"] is False
    assert "body" in read["content"][0]["text"]


async def test_dispatch_prompt_browse_then_get() -> None:
    rt = _primitive_runtime()
    browse = await dispatch_primitive_meta_tool(rt, PROMPT_BROWSE, {"query": "summarize"})
    cards = json.loads(browse["content"][0]["text"])
    assert cards and all(c["kind"] == "prompt" for c in cards)
    got = await dispatch_primitive_meta_tool(
        rt, PROMPT_GET, {"prompt_id": cards[0]["id"], "args": {"text": "hello"}}
    )
    assert got["isError"] is False


async def test_dispatch_prompt_get_missing_arg_errors() -> None:
    rt = _primitive_runtime()
    browse = await dispatch_primitive_meta_tool(rt, PROMPT_BROWSE, {"query": "summarize"})
    pid = json.loads(browse["content"][0]["text"])[0]["id"]
    got = await dispatch_primitive_meta_tool(rt, PROMPT_GET, {"prompt_id": pid, "args": {}})
    assert got["isError"] is True
    assert json.loads(got["content"][0]["text"])["error"] == "ARGS_INVALID"


async def test_dispatch_resource_read_requires_string_id() -> None:
    got = await dispatch_primitive_meta_tool(
        _primitive_runtime(), RESOURCE_READ, {"resource_id": 1}
    )
    assert got["isError"] is True


async def test_dispatch_unknown_meta_tool() -> None:
    got = await dispatch_primitive_meta_tool(_primitive_runtime(), "bogus", {})
    assert got["isError"] is True


# --- server wiring ----------------------------------------------------------


def _tool_runtime() -> ProxyRuntime:
    defs = [{"name": "fs.read", "description": "Read a file.", "inputSchema": {"type": "object"}}]
    rt = ProxyRuntime(StubUpstream(defs))
    rt.register_tool_defs_sync(defs)
    return rt


async def test_server_advertises_tool_and_primitive_meta_tools() -> None:
    tool_rt = _tool_runtime()
    prim_rt = PrimitiveGatewayRuntime(
        StubPrimitiveUpstream(), context_manager=tool_rt.context_manager
    )
    prim_rt.register_sync(RESOURCES, PROMPTS)
    server = McpGatewayServer(tool_rt, primitive_runtime=prim_rt)
    handler = server.server.request_handlers
    # Exercise the registered list_tools handler via the MCP request type.
    from mcp import types as mcp_types

    result = await handler[mcp_types.ListToolsRequest](
        mcp_types.ListToolsRequest(method="tools/list")
    )
    names = {t.name for t in result.root.tools}
    assert set(GATEWAY_TOOL_NAMES) <= names
    assert set(PRIMITIVE_TOOL_NAMES) <= names


async def test_server_without_primitive_runtime_omits_primitive_tools() -> None:
    server = McpGatewayServer(_tool_runtime())
    from mcp import types as mcp_types

    result = await server.server.request_handlers[mcp_types.ListToolsRequest](
        mcp_types.ListToolsRequest(method="tools/list")
    )
    names = {t.name for t in result.root.tools}
    assert not (set(PRIMITIVE_TOOL_NAMES) & names)

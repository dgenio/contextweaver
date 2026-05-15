"""Tests for the transparent MCP proxy adapter (#13)."""

from __future__ import annotations

import json
from typing import Any

from contextweaver.adapters.mcp_proxy import (
    PROXY_META_TOOL_NAMES,
    TOOL_EXECUTE,
    TOOL_HYDRATE,
    dispatch_proxy_request,
    make_proxy_meta_tools,
    make_stripped_tools_list,
)
from contextweaver.adapters.mcp_upstream import StubUpstream
from contextweaver.adapters.proxy_runtime import ExposureMode, ProxyRuntime


def _tool_defs() -> list[dict[str, Any]]:
    return [
        {
            "name": "github.create_issue",
            "description": "Open a new GitHub issue.",
            "inputSchema": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            },
            "_meta": {"version": "1.4.0"},
        }
    ]


async def _ok_handler(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": f"called {name} with {sorted(args.keys())}"}],
        "isError": False,
    }


def _runtime() -> ProxyRuntime:
    runtime = ProxyRuntime(
        StubUpstream(_tool_defs(), handler=_ok_handler),
        mode=ExposureMode.TRANSPARENT,
    )
    runtime.register_tool_defs_sync(_tool_defs())
    return runtime


# ---------------------------------------------------------------------------
# make_stripped_tools_list — §4.1
# ---------------------------------------------------------------------------


def test_stripped_tools_list_includes_catalog_and_meta_tools() -> None:
    runtime = _runtime()
    entries = make_stripped_tools_list(runtime)
    # 1 upstream tool + 2 meta-tools (tool_hydrate, tool_execute).
    assert len(entries) == 3
    names = [e["name"] for e in entries]
    assert TOOL_HYDRATE in names
    assert TOOL_EXECUTE in names


def test_stripped_input_schemas_are_sentinel() -> None:
    runtime = _runtime()
    entries = make_stripped_tools_list(runtime)
    # The catalog entries (everything except the meta-tools) carry
    # ``{"type": "object"}`` with no properties (§4.1 sentinel).
    for entry in entries:
        if entry["name"] in PROXY_META_TOOL_NAMES:
            continue
        assert entry["inputSchema"] == {"type": "object"}


def test_make_proxy_meta_tools_returns_two_tools() -> None:
    runtime = _runtime()
    metas = make_proxy_meta_tools(runtime)
    assert [t["name"] for t in metas] == [TOOL_HYDRATE, TOOL_EXECUTE]


# ---------------------------------------------------------------------------
# dispatch_proxy_request
# ---------------------------------------------------------------------------


async def test_dispatch_tools_list_returns_stripped_catalog() -> None:
    runtime = _runtime()
    result = await dispatch_proxy_request(runtime, "tools/list", {})
    assert "tools" in result
    assert len(result["tools"]) == 3


async def test_dispatch_tool_hydrate_returns_schema() -> None:
    runtime = _runtime()
    tool_id = next(i for i in runtime.list_tool_ids() if i.startswith("github:"))
    result = await dispatch_proxy_request(
        runtime,
        "tools/call",
        {"name": TOOL_HYDRATE, "arguments": {"tool_id": tool_id}},
    )
    assert result["isError"] is False
    body = json.loads(result["content"][0]["text"])
    assert body["tool_id"] == tool_id
    assert "args_schema" in body
    assert "title" in body["args_schema"].get("properties", {})


async def test_dispatch_tool_hydrate_unknown_returns_error() -> None:
    runtime = _runtime()
    result = await dispatch_proxy_request(
        runtime,
        "tools/call",
        {"name": TOOL_HYDRATE, "arguments": {"tool_id": "missing:tool#deadbeef"}},
    )
    assert result["isError"] is True
    body = json.loads(result["content"][0]["text"])
    assert body["error"] == "HYDRATE_FAILED"


async def test_dispatch_tool_execute_validates_args() -> None:
    runtime = _runtime()
    tool_id = next(i for i in runtime.list_tool_ids() if i.startswith("github:"))
    result = await dispatch_proxy_request(
        runtime,
        "tools/call",
        {"name": TOOL_EXECUTE, "arguments": {"tool_id": tool_id, "args": {}}},
    )
    assert result["isError"] is True
    body = json.loads(result["content"][0]["text"])
    assert body["error"] == "ARGS_INVALID"


async def test_dispatch_tool_execute_happy_path() -> None:
    runtime = _runtime()
    tool_id = next(i for i in runtime.list_tool_ids() if i.startswith("github:"))
    result = await dispatch_proxy_request(
        runtime,
        "tools/call",
        {"name": TOOL_EXECUTE, "arguments": {"tool_id": tool_id, "args": {"title": "x"}}},
    )
    assert result["isError"] is False


async def test_dispatch_unsupported_method_returns_error() -> None:
    runtime = _runtime()
    result = await dispatch_proxy_request(runtime, "ping", {})
    assert result["isError"] is True
    body = json.loads(result["content"][0]["text"])
    assert body["error"] == "ARGS_INVALID"


async def test_dispatch_tools_call_requires_name() -> None:
    runtime = _runtime()
    result = await dispatch_proxy_request(runtime, "tools/call", {})
    assert result["isError"] is True

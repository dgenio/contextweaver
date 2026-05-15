"""Tests for the two-tool gateway adapter (#28 + #34)."""

from __future__ import annotations

import json
from typing import Any

from contextweaver.adapters.mcp_gateway import (
    GATEWAY_TOOL_NAMES,
    TOOL_BROWSE,
    TOOL_EXECUTE,
    TOOL_VIEW,
    dispatch_meta_tool,
    make_gateway_meta_tools,
)
from contextweaver.adapters.mcp_upstream import StubUpstream
from contextweaver.adapters.proxy_runtime import ProxyRuntime


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
        "content": [{"type": "text", "text": f"called {name}"}],
        "isError": False,
    }


def _runtime() -> ProxyRuntime:
    runtime = ProxyRuntime(StubUpstream(_tool_defs(), handler=_ok_handler))
    runtime.register_tool_defs_sync(_tool_defs())
    return runtime


# ---------------------------------------------------------------------------
# make_gateway_meta_tools
# ---------------------------------------------------------------------------


def test_make_gateway_meta_tools_returns_three_tools() -> None:
    runtime = _runtime()
    tools = make_gateway_meta_tools(runtime)
    assert [t["name"] for t in tools] == list(GATEWAY_TOOL_NAMES)
    assert {t["name"] for t in tools} == {TOOL_BROWSE, TOOL_EXECUTE, TOOL_VIEW}


def test_gateway_tool_defs_have_no_banned_fields() -> None:
    """§2.2: no banned fields on meta-tool entries either."""
    runtime = _runtime()
    for tool in make_gateway_meta_tools(runtime):
        assert "outputSchema" not in tool
        assert "annotations" not in tool
        assert "_meta" not in tool


# ---------------------------------------------------------------------------
# dispatch_meta_tool — happy paths
# ---------------------------------------------------------------------------


async def test_dispatch_tool_browse_returns_cards_payload() -> None:
    runtime = _runtime()
    result = await dispatch_meta_tool(runtime, TOOL_BROWSE, {"query": "issue"})
    assert result["isError"] is False
    cards = json.loads(result["content"][0]["text"])
    assert isinstance(cards, list)


async def test_dispatch_tool_browse_rejects_both_query_and_path() -> None:
    runtime = _runtime()
    result = await dispatch_meta_tool(runtime, TOOL_BROWSE, {"query": "q", "path": "/x"})
    assert result["isError"] is True
    body = json.loads(result["content"][0]["text"])
    assert body["error"] == "ARGS_INVALID"


async def test_dispatch_tool_execute_validates_args() -> None:
    runtime = _runtime()
    tool_id = next(i for i in runtime.list_tool_ids() if i.startswith("github:"))
    result = await dispatch_meta_tool(runtime, TOOL_EXECUTE, {"tool_id": tool_id, "args": {}})
    assert result["isError"] is True
    body = json.loads(result["content"][0]["text"])
    assert body["error"] == "ARGS_INVALID"


async def test_dispatch_tool_execute_happy_path() -> None:
    runtime = _runtime()
    tool_id = next(i for i in runtime.list_tool_ids() if i.startswith("github:"))
    result = await dispatch_meta_tool(
        runtime, TOOL_EXECUTE, {"tool_id": tool_id, "args": {"title": "x"}}
    )
    assert result["isError"] is False


async def test_dispatch_tool_execute_requires_string_tool_id() -> None:
    runtime = _runtime()
    result = await dispatch_meta_tool(runtime, TOOL_EXECUTE, {"tool_id": 42, "args": {}})
    assert result["isError"] is True


async def test_dispatch_tool_view_invalid_handle() -> None:
    runtime = _runtime()
    result = await dispatch_meta_tool(
        runtime, TOOL_VIEW, {"handle": "missing", "selector": {"type": "head"}}
    )
    assert result["isError"] is True
    body = json.loads(result["content"][0]["text"])
    assert body["error"] == "VIEW_FAILED"


async def test_dispatch_unknown_meta_tool_returns_args_invalid() -> None:
    runtime = _runtime()
    result = await dispatch_meta_tool(runtime, "tool_unknown", {})
    assert result["isError"] is True
    body = json.loads(result["content"][0]["text"])
    assert body["error"] == "ARGS_INVALID"

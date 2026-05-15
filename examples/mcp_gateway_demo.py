"""Two-tool MCP gateway end-to-end demo (#28 + #34).

Demonstrates the three meta-tools the gateway exposes per
``docs/gateway_spec.md`` §4.2:

- ``tool_browse(query|path)`` — returns bounded ChoiceCards.
- ``tool_execute(tool_id, args)`` — validates args, calls upstream,
  applies the context firewall.
- ``tool_view(handle, selector)`` — drills into a previously stored
  artifact (#34).

The demo wires :class:`~contextweaver.adapters.StubUpstream` so it runs
without network access and without an MCP-SDK transport — exactly what
``make example`` exercises in CI.  Swap the upstream for
:class:`~contextweaver.adapters.McpClientUpstream` to front a real MCP
server.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from contextweaver.adapters import (
    GatewayError,
    ProxyRuntime,
    StubUpstream,
    dispatch_meta_tool,
    make_gateway_meta_tools,
)
from contextweaver.envelope import ChoiceCard, ResultEnvelope

UPSTREAM_TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "github.create_issue",
        "description": "Open a new GitHub issue.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["title"],
        },
        "_meta": {"version": "1.4.0"},
    },
    {
        "name": "github.close_issue",
        "description": "Close an existing GitHub issue.",
        "inputSchema": {
            "type": "object",
            "properties": {"issue_id": {"type": "integer"}},
            "required": ["issue_id"],
        },
        "_meta": {"version": "1.4.0"},
    },
    {
        "name": "slack_send_message",
        "description": "Post a message to a Slack channel.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["channel", "text"],
        },
    },
]


async def _stub_handler(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Pretend to invoke an upstream tool — return canned text."""
    if tool_name == "github.create_issue":
        body = "\n".join(
            [
                "issue_id: 142",
                f"title: {args.get('title')!r}",
                f"body: {args.get('body', '<empty>')!r}",
                "status: open",
                "html_url: https://example.com/issues/142",
            ]
        )
        return {"content": [{"type": "text", "text": body}], "isError": False}
    return {
        "content": [{"type": "text", "text": f"stub called {tool_name}"}],
        "isError": False,
    }


async def main() -> int:
    runtime = ProxyRuntime(StubUpstream(UPSTREAM_TOOL_DEFS, handler=_stub_handler))
    runtime.register_tool_defs_sync(UPSTREAM_TOOL_DEFS)

    print("[1/5] Gateway meta-tools advertised to agents:")
    for meta in make_gateway_meta_tools(runtime):
        print(f"      - {meta['name']}: {meta['description'][:60]}…")

    print("\n[2/5] tool_browse(query='open a github issue')")
    cards_payload = await dispatch_meta_tool(
        runtime, "tool_browse", {"query": "open a github issue"}
    )
    cards = json.loads(cards_payload["content"][0]["text"])
    for card in cards[:3]:
        print(f"      [{card['id']}] {card['description'][:48]}")

    print("\n[3/5] tool_browse(path='/')")
    by_path = await dispatch_meta_tool(runtime, "tool_browse", {"path": "/"})
    if by_path["isError"]:
        body = json.loads(by_path["content"][0]["text"])
        print(f"      error: {body['error']} — {body['message']}")
    else:
        for entry in json.loads(by_path["content"][0]["text"])[:5]:
            print(f"      [{entry['id']}] {entry['description'][:48]}")

    print("\n[4/5] tool_execute(github.create_issue)")
    tool_id = next(i for i in runtime.list_tool_ids() if i.startswith("github:create_issue"))
    exec_result = await dispatch_meta_tool(
        runtime,
        "tool_execute",
        {"tool_id": tool_id, "args": {"title": "Demo issue", "body": "Hello"}},
    )
    envelope_dict = json.loads(exec_result["content"][0]["text"])
    print(f"      status={envelope_dict['status']}, summary={envelope_dict['summary'][:60]!r}")

    print("\n[5/5] tool_view over the stored upstream response")
    handles = list(runtime.context_manager.artifact_store.list_refs())
    if handles:
        view_result = await dispatch_meta_tool(
            runtime,
            "tool_view",
            {"handle": handles[0].handle, "selector": {"type": "head", "n_chars": 40}},
        )
        sliced = view_result["content"][0]["text"]
        print(f"      head(40 chars): {sliced!r}")
    else:
        print("      (no artifact persisted — text-only upstream)")

    # Show graceful error handling.
    print("\n[bonus] tool_execute with invalid args returns a structured error")
    bad = await dispatch_meta_tool(runtime, "tool_execute", {"tool_id": tool_id, "args": {}})
    err_body = json.loads(bad["content"][0]["text"])
    assert err_body["error"] == "ARGS_INVALID"
    print(f"      error={err_body['error']}, message={err_body['message'][:60]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))


__all__ = ["ChoiceCard", "GatewayError", "ResultEnvelope", "main"]

"""Transparent MCP proxy demo (#13).

Demonstrates the two surfaces the transparent proxy publishes per
``docs/gateway_spec.md`` §4.1:

1. **Discovery channel** — a stripped ``tools/list`` where every upstream
   tool keeps its canonical ``tool_id`` and description but exposes the
   sentinel ``inputSchema = {"type": "object"}``.
2. **Invocation channel** — two meta-tools: ``tool_hydrate(tool_id)``
   for retrieving the real schema on demand, and
   ``tool_execute(tool_id, args)`` for invoking the upstream tool with
   schema-validated arguments.

The demo uses :class:`~contextweaver.adapters.StubUpstream` so it
exercises every dispatch path without an MCP transport.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from contextweaver.adapters import (
    ExposureMode,
    ProxyRuntime,
    StubUpstream,
    dispatch_proxy_request,
    make_stripped_tools_list,
)

UPSTREAM_TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "filesystem/read",
        "description": "Read a file from the local filesystem.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        "_meta": {"version": "2.0"},
    },
    {
        "name": "filesystem/write",
        "description": "Write bytes to a file on the local filesystem.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
        "_meta": {"version": "2.0"},
    },
]


async def _handler(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    if tool_name == "filesystem/read":
        return {
            "content": [{"type": "text", "text": f"<stub bytes for {args.get('path')!r}>"}],
            "isError": False,
        }
    return {"content": [{"type": "text", "text": "stub write ok"}], "isError": False}


async def main() -> int:
    runtime = ProxyRuntime(
        StubUpstream(UPSTREAM_TOOL_DEFS, handler=_handler),
        mode=ExposureMode.TRANSPARENT,
    )
    runtime.register_tool_defs_sync(UPSTREAM_TOOL_DEFS)

    print("[1/4] Stripped tools/list (proxy discovery channel):")
    for entry in make_stripped_tools_list(runtime):
        schema = entry["inputSchema"]
        print(f"      - {entry['name']}: inputSchema={schema}")

    print("\n[2/4] tool_hydrate retrieves the real schema on demand:")
    tool_id = next(i for i in runtime.list_tool_ids() if i.startswith("filesystem:read"))
    hyd = await dispatch_proxy_request(
        runtime,
        "tools/call",
        {"name": "tool_hydrate", "arguments": {"tool_id": tool_id}},
    )
    body = json.loads(hyd["content"][0]["text"])
    print(f"      schema properties: {list(body['args_schema'].get('properties', {}).keys())}")
    print(f"      required: {body['args_schema'].get('required', [])}")

    print("\n[3/4] tool_execute validates args before upstream dispatch:")
    bad = await dispatch_proxy_request(
        runtime,
        "tools/call",
        {"name": "tool_execute", "arguments": {"tool_id": tool_id, "args": {}}},
    )
    bad_body = json.loads(bad["content"][0]["text"])
    print(f"      missing path → error={bad_body['error']}, message={bad_body['message'][:50]}")

    print("\n[4/4] tool_execute happy path:")
    ok = await dispatch_proxy_request(
        runtime,
        "tools/call",
        {
            "name": "tool_execute",
            "arguments": {"tool_id": tool_id, "args": {"path": "/etc/hostname"}},
        },
    )
    ok_body = json.loads(ok["content"][0]["text"])
    print(f"      status={ok_body['status']}, summary={ok_body['summary'][:50]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))


__all__ = ["main"]

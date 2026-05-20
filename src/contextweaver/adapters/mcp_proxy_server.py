"""Run the transparent MCP proxy (#13) as a real MCP server.

This is the transport-binding layer that lifts
:mod:`contextweaver.adapters.mcp_proxy` onto an
:class:`mcp.server.Server`.  The "pure logic" :mod:`mcp_proxy` module
has no MCP-SDK dependency at construction time and is independently
testable.

The transparent proxy advertises **every** upstream tool — each entry
in ``tools/list`` is a stripped form per §4.1 (sentinel
``inputSchema``).  Schemas are materialised only when the agent calls
``tool_hydrate(tool_id)`` and the proxy executes via
``tool_execute(tool_id, args)``.

Typical usage::

    import asyncio
    from contextweaver.adapters import ProxyRuntime, ExposureMode, StubUpstream
    from contextweaver.adapters.mcp_proxy_server import McpProxyServer

    runtime = ProxyRuntime(StubUpstream([...]), mode=ExposureMode.TRANSPARENT)
    await runtime.refresh_catalog()
    server = McpProxyServer(runtime, name="example-proxy")
    asyncio.run(server.run_stdio())
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

from mcp import types as mcp_types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from contextweaver.adapters.mcp_proxy import (
    PROXY_META_TOOL_NAMES,
    dispatch_proxy_request,
    make_stripped_tools_list,
)
from contextweaver.adapters.proxy_runtime import ExposureMode, ProxyRuntime

logger = logging.getLogger("contextweaver.adapters.mcp_proxy_server")


class McpProxyServer:
    """Binds :mod:`mcp_proxy` onto an :class:`mcp.server.Server`.

    Args:
        runtime: A configured :class:`ProxyRuntime` in
            :attr:`ExposureMode.TRANSPARENT` mode.
        name: MCP server display name.
        version: Optional MCP server version string.
        instructions: Optional human-readable instructions.
    """

    def __init__(
        self,
        runtime: ProxyRuntime,
        *,
        name: str = "contextweaver-proxy",
        version: str | None = None,
        instructions: str | None = None,
    ) -> None:
        if runtime.mode != ExposureMode.TRANSPARENT:
            logger.warning(
                "McpProxyServer received runtime in %s mode; expected TRANSPARENT — "
                "behaviour may differ",
                runtime.mode,
            )
        self.runtime = runtime
        self.server: Server[Any, Any] = Server(name, version=version, instructions=instructions)
        self._register_handlers()

    def _register_handlers(self) -> None:
        async def handle_list_tools() -> list[mcp_types.Tool]:
            return [
                mcp_types.Tool(
                    name=tool["name"],
                    description=tool["description"],
                    inputSchema=tool["inputSchema"],
                )
                for tool in make_stripped_tools_list(self.runtime)
            ]

        async def handle_call_tool(
            name: str, arguments: dict[str, Any] | None
        ) -> mcp_types.CallToolResult:
            # Return a fully-built ``CallToolResult`` so the MCP SDK's
            # call-tool decorator does not try to derive ``structuredContent``
            # from a ``(content, is_error)`` tuple. See the parallel comment
            # in ``mcp_gateway_server.py``.
            #
            # The proxy supports two meta-tools (tool_hydrate / tool_execute).
            # Any other name is treated as a direct upstream call routed via
            # tool_execute(name=tool_id) for the transparent flow.
            if name in PROXY_META_TOOL_NAMES:
                result = await dispatch_proxy_request(
                    self.runtime,
                    "tools/call",
                    {"name": name, "arguments": arguments or {}},
                )
            else:
                # Direct invocation of a stripped catalog entry: treat the
                # MCP tool name as the canonical tool_id and dispatch via
                # tool_execute.
                result = await dispatch_proxy_request(
                    self.runtime,
                    "tools/call",
                    {
                        "name": "tool_execute",
                        "arguments": {
                            "tool_id": name,
                            "args": arguments or {},
                        },
                    },
                )
            content = [
                mcp_types.TextContent(type="text", text=part.get("text", ""))
                for part in result.get("content", [])
                if part.get("type") == "text"
            ]
            if not content:
                content = [
                    mcp_types.TextContent(
                        type="text",
                        text=json.dumps(
                            {"error": "UPSTREAM_ERROR", "message": "empty upstream response"}
                        ),
                    )
                ]
            return mcp_types.CallToolResult(
                content=content,
                isError=bool(result.get("isError", False)),
            )

        # Register handlers by calling the decorators as functions.  See
        # the matching comment in ``mcp_gateway_server.py`` for context.
        cast(Any, self.server).list_tools()(handle_list_tools)
        cast(Any, self.server).call_tool()(handle_call_tool)

    async def run_stdio(self) -> None:
        """Run the proxy over stdio until the client disconnects."""
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                self.server.create_initialization_options(),
            )

"""Run the two-tool gateway (#28) + ``tool_view`` (#34) as a real MCP server.

This is the transport-binding layer that lifts
:mod:`contextweaver.adapters.mcp_gateway` onto an
:class:`mcp.server.Server`.  Keeping it in a separate module preserves
the "pure logic" status of :mod:`mcp_gateway` itself — that module has
no MCP-SDK dependency at construction time and can be tested without
spinning up a server.

Typical usage::

    import asyncio
    from contextweaver.adapters import ProxyRuntime, StubUpstream
    from contextweaver.adapters.mcp_gateway_server import McpGatewayServer

    runtime = ProxyRuntime(StubUpstream([...]))
    server = McpGatewayServer(runtime, name="example-gateway")
    asyncio.run(server.run_stdio())
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

from mcp import types as mcp_types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from contextweaver.adapters.mcp_gateway import (
    GATEWAY_TOOL_NAMES,
    dispatch_meta_tool,
    make_gateway_meta_tools,
)
from contextweaver.adapters.proxy_runtime import ExposureMode, ProxyRuntime

logger = logging.getLogger("contextweaver.adapters.mcp_gateway_server")


class McpGatewayServer:
    """Binds :mod:`mcp_gateway` onto an :class:`mcp.server.Server`.

    Args:
        runtime: A configured :class:`ProxyRuntime`.  The constructor
            sets :attr:`runtime.mode` to :attr:`ExposureMode.GATEWAY` if
            it was not already.
        name: MCP server display name advertised in initialization.
        version: Optional MCP server version string.
        instructions: Optional human-readable instructions advertised to
            the agent.

    Attributes:
        runtime: The wrapped :class:`ProxyRuntime`.
        server: The underlying :class:`mcp.server.Server` with handlers
            wired up.
    """

    def __init__(
        self,
        runtime: ProxyRuntime,
        *,
        name: str = "contextweaver-gateway",
        version: str | None = None,
        instructions: str | None = None,
    ) -> None:
        if runtime.mode != ExposureMode.GATEWAY:
            logger.warning(
                "McpGatewayServer received runtime in %s mode; forcing GATEWAY",
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
                for tool in make_gateway_meta_tools(self.runtime)
            ]

        async def handle_call_tool(
            name: str, arguments: dict[str, Any] | None
        ) -> tuple[list[mcp_types.TextContent], bool]:
            if name not in GATEWAY_TOOL_NAMES:
                payload = json.dumps(
                    {
                        "error": "ARGS_INVALID",
                        "message": f"unknown meta-tool {name!r}",
                    }
                )
                return [mcp_types.TextContent(type="text", text=payload)], True
            result = await dispatch_meta_tool(self.runtime, name, arguments or {})
            content = [
                mcp_types.TextContent(type="text", text=part.get("text", ""))
                for part in result.get("content", [])
                if part.get("type") == "text"
            ]
            return content, bool(result.get("isError", False))

        # Register handlers by calling the decorators as functions.  This
        # avoids the ``Any`` propagation that the MCP SDK's untyped
        # decorator factory triggers under ``mypy --strict``.
        cast(Any, self.server).list_tools()(handle_list_tools)
        cast(Any, self.server).call_tool()(handle_call_tool)

    async def run_stdio(self) -> None:
        """Run the server over stdio until the client disconnects."""
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                self.server.create_initialization_options(),
            )

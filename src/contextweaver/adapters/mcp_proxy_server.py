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

try:
    import uvicorn

    # Probe the remaining soft SSE dependencies so ``_HAS_SSE`` reflects the
    # full set the shared ``_sse_app`` helper needs; the actual binding logic
    # is imported lazily from ``run_sse`` once this flag is True.
    from mcp.server.sse import SseServerTransport  # noqa: F401  (availability probe)
    from starlette.applications import Starlette  # noqa: F401  (availability probe)

    _HAS_SSE = True
except ImportError:  # pragma: no cover
    _HAS_SSE = False

try:
    # Streamable HTTP shares the SSE soft-dependency set (starlette + uvicorn)
    # plus the SDK's session manager; probe it separately so each transport
    # degrades independently. The binding logic is imported lazily from
    # ``run_streamable_http`` once this flag is True.
    from mcp.server.streamable_http_manager import (  # noqa: F401  (availability probe)
        StreamableHTTPSessionManager,
    )

    _HAS_STREAMABLE_HTTP = _HAS_SSE
except ImportError:  # pragma: no cover
    _HAS_STREAMABLE_HTTP = False

from contextweaver.adapters.mcp_proxy import (
    PROXY_META_TOOL_NAMES,
    dispatch_proxy_request,
    make_stripped_tools_list,
)
from contextweaver.adapters.proxy_runtime import ExposureMode, ProxyRuntime
from contextweaver.exceptions import ConfigError

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
            content: list[mcp_types.ContentBlock] = [
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

    async def run_sse(self, host: str = "127.0.0.1", port: int = 8000) -> None:
        """Run the proxy over SSE on *host*:*port* until interrupted.

        Args:
            host: Address to bind (default ``127.0.0.1``).
            port: Port to listen on (default ``8000``).

        Raises:
            ConfigError: If the MCP SDK's SSE dependencies are unavailable.
        """
        if not _HAS_SSE:
            raise ConfigError(
                "SSE transport unavailable. The MCP SDK's SSE support requires "
                "starlette and uvicorn, which should have been installed with "
                "the `mcp` package."
            )
        # Imported lazily (not at module load) so the soft SSE dependency stays
        # optional — this module imports fine without starlette/uvicorn.
        from contextweaver.adapters._sse_app import build_sse_app

        app = build_sse_app(self.server, host=host, port=port)
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        srv = uvicorn.Server(config)
        await srv.serve()

    async def run_streamable_http(
        self,
        host: str = "127.0.0.1",
        port: int = 8000,
        *,
        json_response: bool = False,
        stateless: bool = False,
    ) -> None:
        """Run the proxy over Streamable HTTP on *host*:*port* until interrupted.

        Args:
            host: Address to bind (default ``127.0.0.1``).
            port: Port to listen on (default ``8000``).
            json_response: Answer with plain JSON responses instead of SSE streams.
            stateless: Create a fresh transport per request (no session tracking).

        Raises:
            ConfigError: If the MCP SDK's Streamable HTTP dependencies are
                unavailable.
        """
        if not _HAS_STREAMABLE_HTTP:
            raise ConfigError(
                "Streamable HTTP transport unavailable. The MCP SDK's streamable "
                "HTTP support requires starlette and uvicorn, which should have "
                "been installed with the `mcp` package."
            )
        # Imported lazily (not at module load) so the soft HTTP dependency stays
        # optional — this module imports fine without starlette/uvicorn.
        from contextweaver.adapters._streamable_http_app import build_streamable_http_app

        app = build_streamable_http_app(
            self.server, host=host, port=port, json_response=json_response, stateless=stateless
        )
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        srv = uvicorn.Server(config)
        await srv.serve()

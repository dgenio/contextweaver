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
    # Streamable HTTP needs starlette + uvicorn (the shared ASGI stack) plus
    # the SDK's session manager. Probe every dependency here rather than
    # deriving from ``_HAS_SSE`` so the two transports degrade independently:
    # if SSE probing fails for a reason unrelated to HTTP (or the SDK drops
    # SSE while keeping Streamable HTTP), this flag stays accurate.
    import uvicorn  # noqa: F401  (availability probe)
    from mcp.server.streamable_http_manager import (  # noqa: F401  (availability probe)
        StreamableHTTPSessionManager,
    )
    from starlette.applications import Starlette  # noqa: F401  (availability probe)

    _HAS_STREAMABLE_HTTP = True
except ImportError:  # pragma: no cover
    _HAS_STREAMABLE_HTTP = False

from contextweaver.adapters.gateway_primitives import PrimitiveGatewayRuntime
from contextweaver.adapters.mcp_gateway import (
    GATEWAY_TOOL_NAMES,
    dispatch_meta_tool,
    make_gateway_meta_tools,
)
from contextweaver.adapters.mcp_gateway_primitives import (
    PRIMITIVE_TOOL_NAMES,
    dispatch_primitive_meta_tool,
    make_primitive_meta_tools,
)
from contextweaver.adapters.proxy_runtime import ExposureMode, ProxyRuntime
from contextweaver.exceptions import ConfigError

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
        primitive_runtime: Optional :class:`PrimitiveGatewayRuntime`.  When
            supplied, the server additionally advertises and dispatches the four
            resource/prompt meta-tools (#669 / #670).  Construct it sharing this
            runtime's :class:`~contextweaver.context.manager.ContextManager`
            (``PrimitiveGatewayRuntime(upstream, context_manager=runtime.context_manager)``)
            so reads land in one artifact store / ``tool_view`` surface.

    Attributes:
        runtime: The wrapped :class:`ProxyRuntime`.
        primitive_runtime: The optional :class:`PrimitiveGatewayRuntime`.
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
        primitive_runtime: PrimitiveGatewayRuntime | None = None,
    ) -> None:
        if runtime.mode != ExposureMode.GATEWAY:
            logger.warning(
                "McpGatewayServer received runtime in %s mode; expected GATEWAY — "
                "behaviour may differ",
                runtime.mode,
            )
        self.runtime = runtime
        self.primitive_runtime = primitive_runtime
        self.server: Server[Any, Any] = Server(name, version=version, instructions=instructions)
        self._register_handlers()

    def _register_handlers(self) -> None:
        async def handle_list_tools() -> list[mcp_types.Tool]:
            defs = list(make_gateway_meta_tools(self.runtime))
            if self.primitive_runtime is not None:
                defs += make_primitive_meta_tools()
            return [
                mcp_types.Tool(
                    name=tool["name"],
                    description=tool["description"],
                    inputSchema=tool["inputSchema"],
                )
                for tool in defs
            ]

        async def handle_call_tool(
            name: str, arguments: dict[str, Any] | None
        ) -> mcp_types.CallToolResult:
            # Return a fully-built ``CallToolResult`` so the MCP SDK's
            # call-tool decorator does not try to derive ``structuredContent``
            # from a ``(content, is_error)`` tuple — the gateway's
            # ``tool_browse`` payload is a JSON array which fails
            # structured-content validation (must be a dict).
            if name in GATEWAY_TOOL_NAMES:
                result = await dispatch_meta_tool(self.runtime, name, arguments or {})
            elif self.primitive_runtime is not None and name in PRIMITIVE_TOOL_NAMES:
                result = await dispatch_primitive_meta_tool(
                    self.primitive_runtime, name, arguments or {}
                )
            else:
                payload = json.dumps(
                    {"error": "ARGS_INVALID", "message": f"unknown meta-tool {name!r}"}
                )
                return mcp_types.CallToolResult(
                    content=[mcp_types.TextContent(type="text", text=payload)],
                    isError=True,
                )
            content: list[mcp_types.ContentBlock] = [
                mcp_types.TextContent(type="text", text=part.get("text", ""))
                for part in result.get("content", [])
                if part.get("type") == "text"
            ]
            return mcp_types.CallToolResult(
                content=content,
                isError=bool(result.get("isError", False)),
            )

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

    async def run_sse(self, host: str = "127.0.0.1", port: int = 8000) -> None:
        """Run the server over SSE on *host*:*port* until interrupted.

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
        """Run the server over Streamable HTTP on *host*:*port* until interrupted.

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

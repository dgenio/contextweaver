"""Shared Streamable HTTP ASGI binding for the MCP server adapters (issue #422).

The gateway and proxy servers expose the same MCP surface over the Streamable
HTTP transport (the successor to SSE in the MCP spec), so the session-manager
construction, transport-security configuration, and route wiring live here as
a single implementation they both call. Keeping it in one place keeps the
security defaults consistent across both servers and makes the wiring
unit-testable without starting a real ``uvicorn`` process.

This module imports the soft HTTP dependencies (``starlette``,
``mcp.server.streamable_http_manager``) at import time, so it must only be
imported *after* a caller has confirmed those dependencies are present — see
``_HAS_STREAMABLE_HTTP`` in
:mod:`contextweaver.adapters.mcp_gateway_server` /
:mod:`contextweaver.adapters.mcp_proxy_server`, which import this module
lazily from ``run_streamable_http`` for exactly that reason.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Mount

# The DNS-rebinding allowlist logic is transport-agnostic (it scopes the
# ``Host`` / ``Origin`` allowlists to the bind address), so the streamable
# HTTP binding reuses the SSE helper rather than duplicating it. ``_sse_app``
# needs no dependency beyond starlette + the ``mcp`` package, both of which
# this module already requires.
from contextweaver.adapters._sse_app import sse_security_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from mcp.server import Server


def build_streamable_http_app(
    server: Server[Any, Any],
    *,
    host: str,
    port: int,
    streamable_http_path: str = "/mcp",
    json_response: bool = False,
    stateless: bool = False,
) -> Starlette:
    """Construct the Starlette ASGI app that binds *server* over Streamable HTTP.

    Mirrors :func:`contextweaver.adapters._sse_app.build_sse_app` for the
    Streamable HTTP transport: the route wiring is unit-testable without
    starting ``uvicorn``. DNS-rebinding protection is enabled and scoped to
    *host* via :func:`~contextweaver.adapters._sse_app.sse_security_settings`
    (the MCP SDK leaves it disabled by default).

    The returned app owns a :class:`StreamableHTTPSessionManager` whose
    lifecycle is bound to the app's lifespan — the app must be served (or its
    lifespan entered) before requests are handled, and a given app instance
    cannot be restarted after its lifespan exits (an SDK session-manager
    constraint); build a fresh app instead.

    Args:
        server: The MCP server whose ``run`` drives each session.
        host: Bind address, used to scope DNS-rebinding protection.
        port: Bind port, used to scope DNS-rebinding protection.
        streamable_http_path: Mount path for the MCP endpoint (default ``/mcp``).
        json_response: If ``True``, the server answers with plain JSON responses
            instead of SSE streams (SDK ``json_response`` option).
        stateless: If ``True``, each request gets a fresh transport with no
            session tracking (SDK ``stateless`` option); no ``mcp-session-id``
            header is issued.

    Returns:
        A configured :class:`~starlette.applications.Starlette` app exposing
        the MCP Streamable HTTP endpoint at *streamable_http_path*.
    """
    manager = StreamableHTTPSessionManager(
        app=server,
        json_response=json_response,
        stateless=stateless,
        security_settings=sse_security_settings(host, port),
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with manager.run():
            yield

    return Starlette(
        routes=[Mount(streamable_http_path, app=manager.handle_request)],
        lifespan=lifespan,
    )

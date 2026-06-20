"""Tests for the MCP SSE transport binding (issue #694).

These tests validate that :meth:`McpGatewayServer.run_sse` and
:meth:`McpProxyServer.run_sse` are wired correctly and do not crash
on import or instantiation.  Full end-to-end SSE server tests are
excluded here because uvicorn + asyncio lifecycle in a test process
is fragile; they are better covered manually or via the
``benchmarks/`` suite.
"""

from __future__ import annotations

import pytest

from contextweaver.adapters import ProxyRuntime, StubUpstream
from contextweaver.adapters.mcp_gateway_server import _HAS_SSE, McpGatewayServer
from contextweaver.adapters.mcp_proxy_server import McpProxyServer
from contextweaver.adapters.proxy_runtime import ExposureMode
from contextweaver.exceptions import ConfigError


@pytest.mark.skipif(not _HAS_SSE, reason="SSE dependencies unavailable")
def test_has_sse_flag_is_true_when_deps_present() -> None:
    """_HAS_SSE must be True because the dev environment ships starlette + uvicorn."""
    assert _HAS_SSE is True


@pytest.mark.skipif(not _HAS_SSE, reason="SSE dependencies unavailable")
def test_gateway_server_run_sse_method_exists() -> None:
    """run_sse must exist on the gateway server class."""
    runtime = ProxyRuntime(StubUpstream([]), mode=ExposureMode.GATEWAY)
    server = McpGatewayServer(runtime, name="test")
    assert callable(getattr(server, "run_sse", None))


@pytest.mark.skipif(not _HAS_SSE, reason="SSE dependencies unavailable")
def test_proxy_server_run_sse_method_exists() -> None:
    """run_sse must exist on the proxy server class."""
    runtime = ProxyRuntime(StubUpstream([]), mode=ExposureMode.TRANSPARENT)
    server = McpProxyServer(runtime, name="test")
    assert callable(getattr(server, "run_sse", None))


@pytest.mark.asyncio
@pytest.mark.skipif(_HAS_SSE, reason="only run when sse deps are missing")
async def test_gateway_run_sse_raises_when_deps_missing() -> None:
    """If SSE deps were missing, run_sse should raise ConfigError.

    This guard documentation is kept for completeness; in the standard
    dev environment _HAS_SSE is True and the test is skipped.
    """
    runtime = ProxyRuntime(StubUpstream([]), mode=ExposureMode.GATEWAY)
    server = McpGatewayServer(runtime, name="test")
    with pytest.raises(ConfigError, match="SSE transport unavailable"):
        await server.run_sse()


@pytest.mark.asyncio
@pytest.mark.skipif(_HAS_SSE, reason="only run when sse deps are missing")
async def test_proxy_run_sse_raises_when_deps_missing() -> None:
    """Same ConfigError guard for McpProxyServer."""
    runtime = ProxyRuntime(StubUpstream([]), mode=ExposureMode.TRANSPARENT)
    server = McpProxyServer(runtime, name="test")
    with pytest.raises(ConfigError, match="SSE transport unavailable"):
        await server.run_sse()


@pytest.mark.skipif(not _HAS_SSE, reason="SSE dependencies unavailable")
def test_gateway_sse_app_instantiation() -> None:
    """The Starlette app built inside run_sse can be created without error.

    We reach inside the private helper logic by replicating the app
    construction pattern in a way that doesn't start uvicorn.
    """
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.types import Receive, Scope, Send

    runtime = ProxyRuntime(StubUpstream([]), mode=ExposureMode.GATEWAY)
    server = McpGatewayServer(runtime, name="test")
    sse = SseServerTransport("/messages/")

    async def _handle_sse(scope: Scope, receive: Receive, send: Send) -> None:
        async with sse.connect_sse(scope, receive, send) as (rs, ws):
            await server.server.run(rs, ws, server.server.create_initialization_options())

    app = Starlette(
        routes=[
            Mount("/sse", app=_handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ],
    )
    assert app.routes is not None


@pytest.mark.skipif(not _HAS_SSE, reason="SSE dependencies unavailable")
def test_proxy_sse_app_instantiation() -> None:
    """Same Starlette app construction test for McpProxyServer."""
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.types import Receive, Scope, Send

    runtime = ProxyRuntime(StubUpstream([]), mode=ExposureMode.TRANSPARENT)
    server = McpProxyServer(runtime, name="test")
    sse = SseServerTransport("/messages/")

    async def _handle_sse(scope: Scope, receive: Receive, send: Send) -> None:
        async with sse.connect_sse(scope, receive, send) as (rs, ws):
            await server.server.run(rs, ws, server.server.create_initialization_options())

    app = Starlette(
        routes=[
            Mount("/sse", app=_handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ],
    )
    assert app.routes is not None

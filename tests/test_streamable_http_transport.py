"""Tests for the MCP Streamable HTTP transport binding (issue #422).

These tests validate that :meth:`McpGatewayServer.run_streamable_http` and
:meth:`McpProxyServer.run_streamable_http` are wired correctly, and exercise
a full in-process initialize round-trip over the real SDK transport. Unlike
the SSE suite (``test_mcp_transport.py``), no ``uvicorn`` process is needed:
the MCP SDK's ``streamable_http_client`` accepts a pre-configured
``httpx.AsyncClient``, so the client is pointed at the Starlette app through
``httpx.ASGITransport`` with the app's lifespan entered manually (the ASGI
transport does not run lifespans itself).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import pytest

from contextweaver.adapters import ProxyRuntime, StubUpstream
from contextweaver.adapters.mcp_gateway_server import _HAS_STREAMABLE_HTTP, McpGatewayServer
from contextweaver.adapters.mcp_proxy_server import McpProxyServer
from contextweaver.adapters.proxy_runtime import ExposureMode
from contextweaver.exceptions import ConfigError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import httpx

#: Base URL the in-process client targets. The host must match the bind host
#: passed to ``build_streamable_http_app`` so the DNS-rebinding allowlist
#: accepts the requests.
BASE_URL = "http://127.0.0.1:8000"


@asynccontextmanager
async def _asgi_client(app: object) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an httpx client bound to *app* with the app lifespan running.

    ``follow_redirects=True`` mirrors the MCP SDK's default client
    (``create_mcp_http_client``): Starlette's ``Mount("/mcp")`` 307-redirects
    ``/mcp`` to ``/mcp/``.
    """
    import httpx

    async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
        transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(
            transport=transport, base_url=BASE_URL, follow_redirects=True
        ) as client:
            yield client


@pytest.mark.skipif(not _HAS_STREAMABLE_HTTP, reason="Streamable HTTP dependencies unavailable")
def test_has_streamable_http_flag_is_true_when_deps_present() -> None:
    """_HAS_STREAMABLE_HTTP must be True: the dev environment ships starlette + uvicorn."""
    assert _HAS_STREAMABLE_HTTP is True


@pytest.mark.skipif(not _HAS_STREAMABLE_HTTP, reason="Streamable HTTP dependencies unavailable")
def test_gateway_server_run_streamable_http_method_exists() -> None:
    """run_streamable_http must exist on the gateway server class."""
    runtime = ProxyRuntime(StubUpstream([]), mode=ExposureMode.GATEWAY)
    server = McpGatewayServer(runtime, name="test")
    assert callable(getattr(server, "run_streamable_http", None))


@pytest.mark.skipif(not _HAS_STREAMABLE_HTTP, reason="Streamable HTTP dependencies unavailable")
def test_proxy_server_run_streamable_http_method_exists() -> None:
    """run_streamable_http must exist on the proxy server class."""
    runtime = ProxyRuntime(StubUpstream([]), mode=ExposureMode.TRANSPARENT)
    server = McpProxyServer(runtime, name="test")
    assert callable(getattr(server, "run_streamable_http", None))


@pytest.mark.asyncio
@pytest.mark.skipif(_HAS_STREAMABLE_HTTP, reason="only run when streamable HTTP deps are missing")
async def test_gateway_run_streamable_http_raises_when_deps_missing() -> None:
    """If the deps were missing, run_streamable_http should raise ConfigError.

    This guard documentation is kept for completeness; in the standard dev
    environment _HAS_STREAMABLE_HTTP is True and the test is skipped.
    """
    runtime = ProxyRuntime(StubUpstream([]), mode=ExposureMode.GATEWAY)
    server = McpGatewayServer(runtime, name="test")
    with pytest.raises(ConfigError, match="Streamable HTTP transport unavailable"):
        await server.run_streamable_http()


@pytest.mark.asyncio
@pytest.mark.skipif(_HAS_STREAMABLE_HTTP, reason="only run when streamable HTTP deps are missing")
async def test_proxy_run_streamable_http_raises_when_deps_missing() -> None:
    """Same ConfigError guard for McpProxyServer."""
    runtime = ProxyRuntime(StubUpstream([]), mode=ExposureMode.TRANSPARENT)
    server = McpProxyServer(runtime, name="test")
    with pytest.raises(ConfigError, match="Streamable HTTP transport unavailable"):
        await server.run_streamable_http()


def _mount_paths(app: object) -> set[str]:
    """Collect the mount paths from a Starlette app's routes."""
    return {getattr(route, "path", "") for route in app.routes}  # type: ignore[attr-defined]


@pytest.mark.skipif(not _HAS_STREAMABLE_HTTP, reason="Streamable HTTP dependencies unavailable")
def test_build_streamable_http_app_wires_route_for_gateway() -> None:
    """build_streamable_http_app mounts the MCP endpoint at /mcp.

    Exercises the real helper rather than replicating its body, so a
    regression in the route wiring is caught — without starting uvicorn.
    """
    from contextweaver.adapters._streamable_http_app import build_streamable_http_app

    runtime = ProxyRuntime(StubUpstream([]), mode=ExposureMode.GATEWAY)
    server = McpGatewayServer(runtime, name="test")
    app = build_streamable_http_app(server.server, host="127.0.0.1", port=8000)
    assert _mount_paths(app) == {"/mcp"}


@pytest.mark.skipif(not _HAS_STREAMABLE_HTTP, reason="Streamable HTTP dependencies unavailable")
def test_build_streamable_http_app_wires_route_for_proxy() -> None:
    """Same route-wiring check for the proxy server path."""
    from contextweaver.adapters._streamable_http_app import build_streamable_http_app

    runtime = ProxyRuntime(StubUpstream([]), mode=ExposureMode.TRANSPARENT)
    server = McpProxyServer(runtime, name="test")
    app = build_streamable_http_app(server.server, host="127.0.0.1", port=8000)
    assert _mount_paths(app) == {"/mcp"}


@pytest.mark.asyncio
@pytest.mark.skipif(not _HAS_STREAMABLE_HTTP, reason="Streamable HTTP dependencies unavailable")
async def test_gateway_initialize_round_trip_over_streamable_http() -> None:
    """A real SDK client completes initialize + tools/list against the gateway app."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    from contextweaver.adapters._streamable_http_app import build_streamable_http_app

    runtime = ProxyRuntime(StubUpstream([]), mode=ExposureMode.GATEWAY)
    server = McpGatewayServer(runtime, name="streamable-gateway")
    app = build_streamable_http_app(server.server, host="127.0.0.1", port=8000)
    async with (
        _asgi_client(app) as client,
        streamable_http_client(f"{BASE_URL}/mcp", http_client=client) as (
            read_stream,
            write_stream,
            get_session_id,
        ),
        ClientSession(read_stream, write_stream) as session,
    ):
        result = await session.initialize()
        assert result.protocolVersion
        assert result.serverInfo.name == "streamable-gateway"
        assert get_session_id() is not None
        tools = await session.list_tools()
        names = {tool.name for tool in tools.tools}
        assert names == {"tool_browse", "tool_execute", "tool_view"}


@pytest.mark.asyncio
@pytest.mark.skipif(not _HAS_STREAMABLE_HTTP, reason="Streamable HTTP dependencies unavailable")
async def test_proxy_initialize_round_trip_over_streamable_http() -> None:
    """A real SDK client completes initialize + tools/list against the proxy app."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    from contextweaver.adapters._streamable_http_app import build_streamable_http_app

    runtime = ProxyRuntime(StubUpstream([]), mode=ExposureMode.TRANSPARENT)
    await runtime.refresh_catalog()
    server = McpProxyServer(runtime, name="streamable-proxy")
    app = build_streamable_http_app(server.server, host="127.0.0.1", port=8000)
    async with (
        _asgi_client(app) as client,
        streamable_http_client(f"{BASE_URL}/mcp", http_client=client) as (
            read_stream,
            write_stream,
            get_session_id,
        ),
        ClientSession(read_stream, write_stream) as session,
    ):
        result = await session.initialize()
        assert result.serverInfo.name == "streamable-proxy"
        assert get_session_id() is not None
        tools = await session.list_tools()
        # Empty stub upstream: only the proxy's own meta-tools are
        # advertised, and the round-trip itself must succeed.
        names = {tool.name for tool in tools.tools}
        assert names == {"tool_hydrate", "tool_execute"}

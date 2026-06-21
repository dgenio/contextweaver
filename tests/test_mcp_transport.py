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


def _mount_paths(app: object) -> set[str]:
    """Collect the mount paths from a Starlette app's routes."""
    return {getattr(route, "path", "") for route in app.routes}  # type: ignore[attr-defined]


@pytest.mark.skipif(not _HAS_SSE, reason="SSE dependencies unavailable")
def test_build_sse_app_wires_routes_for_gateway() -> None:
    """build_sse_app (used by McpGatewayServer.run_sse) wires both SSE routes.

    Exercises the real helper rather than replicating its body, so a regression
    in the route wiring is caught — without starting uvicorn.
    """
    from contextweaver.adapters._sse_app import build_sse_app

    runtime = ProxyRuntime(StubUpstream([]), mode=ExposureMode.GATEWAY)
    server = McpGatewayServer(runtime, name="test")
    app = build_sse_app(server.server, host="127.0.0.1", port=8000)
    assert _mount_paths(app) == {"/sse", "/messages"}


@pytest.mark.skipif(not _HAS_SSE, reason="SSE dependencies unavailable")
def test_build_sse_app_wires_routes_for_proxy() -> None:
    """Same route-wiring check for the proxy server path."""
    from contextweaver.adapters._sse_app import build_sse_app

    runtime = ProxyRuntime(StubUpstream([]), mode=ExposureMode.TRANSPARENT)
    server = McpProxyServer(runtime, name="test")
    app = build_sse_app(server.server, host="127.0.0.1", port=8000)
    assert _mount_paths(app) == {"/sse", "/messages"}


@pytest.mark.skipif(not _HAS_SSE, reason="SSE dependencies unavailable")
def test_sse_security_settings_enable_dns_rebinding_protection() -> None:
    """SSE binding enables DNS-rebinding protection scoped to the bind host.

    The MCP SDK disables this by default, so the regression guard is that
    contextweaver turns it on and scopes the Host allowlist to host:port
    (with localhost aliases for loopback binds).
    """
    from contextweaver.adapters._sse_app import sse_security_settings

    settings = sse_security_settings("127.0.0.1", 8080)
    assert settings.enable_dns_rebinding_protection is True
    assert "127.0.0.1:8080" in settings.allowed_hosts
    assert "localhost:8080" in settings.allowed_hosts

    # A non-loopback bind is scoped to exactly that host (no localhost aliases).
    public = sse_security_settings("example.com", 9000)
    assert public.enable_dns_rebinding_protection is True
    assert "example.com:9000" in public.allowed_hosts
    assert "localhost:9000" not in public.allowed_hosts

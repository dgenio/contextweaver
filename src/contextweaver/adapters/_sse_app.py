"""Shared SSE (Server-Sent Events) ASGI binding for the MCP server adapters.

The gateway and proxy servers expose the same MCP surface over SSE, so the
transport-security configuration and route wiring live here as a single
implementation they both call. Keeping it in one place keeps the security
defaults consistent across both servers and makes the wiring unit-testable
without starting a real ``uvicorn`` process.

This module imports the soft SSE dependencies (``starlette``,
``mcp.server.sse``) at import time, so it must only be imported *after* a caller
has confirmed those dependencies are present — see ``_HAS_SSE`` in
:mod:`contextweaver.adapters.mcp_gateway_server` /
:mod:`contextweaver.adapters.mcp_proxy_server`, which import this module lazily
from ``run_sse`` for exactly that reason.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.server.sse import SseServerTransport
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.types import Receive, Scope, Send

if TYPE_CHECKING:
    from mcp.server import Server

#: Bind addresses that also accept the usual ``localhost`` aliases when
#: validating the ``Host`` header. ``0.0.0.0`` means "all interfaces", so a
#: loopback client still reaches it as ``127.0.0.1`` / ``localhost``.
_LOOPBACK_HOSTS = frozenset({"0.0.0.0", "127.0.0.1", "::1", "localhost"})


def sse_security_settings(host: str, port: int) -> TransportSecuritySettings:
    """Build DNS-rebinding protection scoped to the bind address.

    The MCP SDK leaves DNS-rebinding protection **disabled** unless explicit
    settings are supplied (``TransportSecurityMiddleware`` defaults to
    ``enable_dns_rebinding_protection=False`` for backwards compatibility), so
    we enable it here and allow only the configured ``host`` (the SDK's
    ``host:*`` pattern accepts any port). Loopback binds additionally accept the
    ``localhost`` / ``127.0.0.1`` aliases. A public bind (e.g. ``0.0.0.0`` or a
    routable host) should sit behind a reverse proxy that forwards a ``Host`` in
    this allowlist — see ``docs/integration_mcp.md``.

    Args:
        host: The address ``run_sse`` binds to.
        port: The port ``run_sse`` binds to.

    Returns:
        Settings with DNS-rebinding protection enabled and the ``Host`` /
        ``Origin`` allowlists scoped to *host*.
    """
    hosts = {f"{host}:{port}", f"{host}:*"}
    origins = {f"http://{host}:{port}", f"https://{host}:{port}"}
    if host in _LOOPBACK_HOSTS:
        for alias in ("localhost", "127.0.0.1"):
            hosts.update({f"{alias}:{port}", f"{alias}:*"})
            origins.update({f"http://{alias}:{port}", f"https://{alias}:{port}"})
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=sorted(hosts),
        allowed_origins=sorted(origins),
    )


def build_sse_app(
    server: Server[Any, Any],
    *,
    host: str,
    port: int,
    messages_path: str = "/messages/",
) -> Starlette:
    """Construct the Starlette ASGI app that binds *server* over SSE.

    Extracted from ``run_sse`` so the route wiring is unit-testable without
    starting ``uvicorn``. DNS-rebinding protection is enabled and scoped to
    *host* via :func:`sse_security_settings`.

    Args:
        server: The MCP server whose ``run`` drives each SSE session.
        host: Bind address, used to scope DNS-rebinding protection.
        port: Bind port, used to scope DNS-rebinding protection.
        messages_path: Mount path for the client-to-server POST channel.

    Returns:
        A configured :class:`~starlette.applications.Starlette` app exposing
        ``/sse`` (the event stream) and *messages_path* (the POST channel).
    """
    sse = SseServerTransport(
        messages_path,
        security_settings=sse_security_settings(host, port),
    )

    async def _handle_sse(scope: Scope, receive: Receive, send: Send) -> None:
        async with sse.connect_sse(scope, receive, send) as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    return Starlette(
        routes=[
            Mount("/sse", app=_handle_sse),
            Mount(messages_path, app=sse.handle_post_message),
        ],
    )

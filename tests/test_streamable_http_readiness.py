"""Streamable HTTP readiness / conformance suite (issue #665).

Raw-HTTP conformance checks over the gateway's Streamable HTTP binding,
asserting the wire-level behaviour a remote MCP client depends on:
protocol-version negotiation, ``mcp-session-id`` issuance and enforcement,
per-connection session isolation, and DNS-rebinding rejection.

The checks deliberately speak plain JSON-RPC-over-HTTP (via
``httpx.ASGITransport``) instead of the SDK client so each header behaviour is
asserted explicitly. Expected status codes are the installed MCP SDK's actual
behaviour (``mcp.server.streamable_http`` / ``streamable_http_manager``):

* non-initialize POST without a session id → 400 (transport rejects it)
* POST with an unknown session id → 404 (manager: "Session not found")
* disallowed ``Host`` header → 421 (``TransportSecurityMiddleware``)
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import pytest

from contextweaver.adapters import ProxyRuntime, StubUpstream
from contextweaver.adapters.mcp_gateway_server import _HAS_STREAMABLE_HTTP, McpGatewayServer
from contextweaver.adapters.proxy_runtime import ExposureMode

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import httpx

pytestmark = pytest.mark.skipif(
    not _HAS_STREAMABLE_HTTP, reason="Streamable HTTP dependencies unavailable"
)

#: Base URL matching the bind host, so the DNS-rebinding allowlist accepts it.
BASE_URL = "http://127.0.0.1:8000"
ENDPOINT = f"{BASE_URL}/mcp/"
SESSION_HEADER = "mcp-session-id"

#: Headers every Streamable HTTP POST must carry per the MCP spec.
POST_HEADERS = {
    "accept": "application/json, text/event-stream",
    "content-type": "application/json",
}


def _initialize_payload(request_id: int = 1) -> dict[str, Any]:
    """A spec-shaped ``initialize`` request advertising the SDK's latest version."""
    from mcp.types import LATEST_PROTOCOL_VERSION

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {
            "protocolVersion": LATEST_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "readiness-suite", "version": "0"},
        },
    }


def _parse_message(response: httpx.Response) -> dict[str, Any]:
    """Extract the first JSON-RPC message from a JSON or SSE response body."""
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        return dict(response.json())
    for line in response.text.splitlines():
        if line.startswith("data:"):
            return dict(json.loads(line[len("data:") :].strip()))
    raise AssertionError(f"no JSON-RPC message in response body: {response.text!r}")


@asynccontextmanager
async def _gateway_client(
    *, json_response: bool = False, stateless: bool = False
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an httpx client against a fresh gateway app with its lifespan running."""
    import httpx

    from contextweaver.adapters._streamable_http_app import build_streamable_http_app

    runtime = ProxyRuntime(StubUpstream([]), mode=ExposureMode.GATEWAY)
    server = McpGatewayServer(runtime, name="readiness-gateway")
    app = build_streamable_http_app(
        server.server,
        host="127.0.0.1",
        port=8000,
        json_response=json_response,
        stateless=stateless,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
            yield client


async def _initialize(client: httpx.AsyncClient) -> httpx.Response:
    """POST an ``initialize`` request and return the raw response."""
    return await client.post(ENDPOINT, headers=POST_HEADERS, json=_initialize_payload())


@pytest.mark.asyncio
async def test_initialize_negotiates_protocol_version() -> None:
    """initialize succeeds and returns a protocol version the SDK supports (#665.1)."""
    from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS

    async with _gateway_client() as client:
        response = await _initialize(client)
        assert response.status_code == 200
        message = _parse_message(response)
        negotiated = message["result"]["protocolVersion"]
        assert negotiated in SUPPORTED_PROTOCOL_VERSIONS


@pytest.mark.asyncio
async def test_session_id_issued_and_honored_on_subsequent_requests() -> None:
    """initialize issues an mcp-session-id that later requests can present (#665.2)."""
    async with _gateway_client() as client:
        init_response = await _initialize(client)
        assert init_response.status_code == 200
        session_id = init_response.headers.get(SESSION_HEADER)
        assert session_id
        negotiated = _parse_message(init_response)["result"]["protocolVersion"]

        session_headers = {
            **POST_HEADERS,
            SESSION_HEADER: session_id,
            "mcp-protocol-version": negotiated,
        }
        notified = await client.post(
            ENDPOINT,
            headers=session_headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        assert notified.status_code == 202

        listed = await client.post(
            ENDPOINT,
            headers=session_headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        assert listed.status_code == 200
        message = _parse_message(listed)
        assert message["id"] == 2
        assert "tools" in message["result"]


@pytest.mark.asyncio
async def test_second_initialize_creates_distinct_session() -> None:
    """A reconnecting client (fresh initialize) gets a distinct session id (#665.3)."""
    async with _gateway_client() as client:
        first = await _initialize(client)
        second = await _initialize(client)
        assert first.status_code == 200
        assert second.status_code == 200
        first_id = first.headers.get(SESSION_HEADER)
        second_id = second.headers.get(SESSION_HEADER)
        assert first_id and second_id
        assert first_id != second_id


@pytest.mark.asyncio
async def test_request_without_session_id_rejected_after_init() -> None:
    """A non-initialize request that omits the session id is rejected with 400 (#665.4).

    The installed SDK enforces this in
    ``StreamableHTTPServerTransport._validate_session`` ("Bad Request: Missing
    session ID"); the manager never routes the request to the initialized
    session.
    """
    async with _gateway_client() as client:
        init_response = await _initialize(client)
        assert init_response.headers.get(SESSION_HEADER)

        response = await client.post(
            ENDPOINT,
            headers=POST_HEADERS,  # deliberately no mcp-session-id
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        assert response.status_code == 400
        assert "session" in response.text.lower()


@pytest.mark.asyncio
async def test_unknown_session_id_rejected_with_404() -> None:
    """A request presenting a session id the server never issued gets 404 (#665.4).

    The installed SDK's session manager answers unknown/expired session ids
    with 404 "Session not found" per the MCP spec.
    """
    async with _gateway_client() as client:
        await _initialize(client)
        response = await client.post(
            ENDPOINT,
            headers={**POST_HEADERS, SESSION_HEADER: "deadbeef" * 4},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        assert response.status_code == 404
        message = _parse_message(response)
        assert "not found" in message["error"]["message"].lower()


@pytest.mark.asyncio
async def test_hostile_host_header_rejected() -> None:
    """DNS-rebinding protection rejects a Host header outside the allowlist (#665.5).

    ``build_streamable_http_app`` scopes the allowlist to the bind host, so a
    rebound ``Host`` must be answered with 421 Misdirected Request by the
    SDK's ``TransportSecurityMiddleware``.
    """
    async with _gateway_client() as client:
        response = await client.post(
            ENDPOINT,
            headers={**POST_HEADERS, "host": "evil.example.com:8000"},
            json=_initialize_payload(),
        )
        assert response.status_code == 421
        assert "host" in response.text.lower()


@pytest.mark.asyncio
async def test_stateless_mode_issues_no_session_id() -> None:
    """SDK capability check: ``stateless=True`` disables session tracking.

    Documents the ``StreamableHTTPSessionManager(stateless=...)`` option the
    binding exposes: no ``mcp-session-id`` header is issued.
    """
    async with _gateway_client(stateless=True) as client:
        response = await _initialize(client)
        assert response.status_code == 200
        assert response.headers.get(SESSION_HEADER) is None


@pytest.mark.asyncio
async def test_json_response_mode_returns_plain_json() -> None:
    """SDK capability check: ``json_response=True`` answers with JSON, not SSE.

    Documents the ``StreamableHTTPSessionManager(json_response=...)`` option
    the binding exposes.
    """
    async with _gateway_client(json_response=True) as client:
        response = await _initialize(client)
        assert response.status_code == 200
        assert response.headers.get("content-type", "").startswith("application/json")
        assert _parse_message(response)["result"]["protocolVersion"]

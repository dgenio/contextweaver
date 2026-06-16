"""Stdlib HTTP transport for the sidecar (private plumbing, issue #675).

Binds a :class:`~contextweaver.adapters.sidecar.SidecarApp` onto Python's
``http.server.ThreadingHTTPServer`` so the route/compact API is reachable over
plain HTTP/JSON with **no third-party dependency** — consistent with the repo's
minimal-core-deps policy (``AGENTS.md`` → Coding Style).  The handler is a thin
shim: it reads the body, delegates to :meth:`SidecarApp.dispatch`, and writes the
returned ``(status, json_body)``.  All policy (auth, rate limit, validation)
lives in the app, not here.

Not public API: import the public :func:`contextweaver.adapters.serve_api` /
:func:`make_sidecar_server` re-exports instead.
"""

from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from contextweaver.adapters.sidecar import SidecarApp

_logger = logging.getLogger("contextweaver.adapters.sidecar")

#: Cap on the body we are willing to read off the socket before the app's own
#: ``max_body_bytes`` check runs — a coarse first line of defence so a missing
#: ``Content-Length`` or a hostile large body cannot exhaust memory.
_READ_CAP = 8 * 1_048_576


class _SidecarServer(ThreadingHTTPServer):
    """Threading HTTP server carrying the :class:`SidecarApp` for its handlers."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], app: SidecarApp) -> None:
        """Bind *app* to *server_address* before the base server starts."""
        self.app = app
        super().__init__(server_address, _SidecarHandler)


class _SidecarHandler(BaseHTTPRequestHandler):
    """Translate one HTTP request into a :meth:`SidecarApp.dispatch` call."""

    server_version = "contextweaver-sidecar/1"
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler dispatch name
        """Handle a GET (health/liveness)."""
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler dispatch name
        """Handle a POST (route/compact)."""
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        app: SidecarApp = self.server.app  # type: ignore[attr-defined]
        path = self.path.split("?", 1)[0]
        headers = {k: v for k, v in self.headers.items()}
        body = self._read_body()
        client_id = _client_id(headers, self.client_address)
        try:
            status, payload = app.dispatch(method, path, headers, body, client_id=client_id)
        except Exception:  # noqa: BLE001 — never leak a traceback over the wire
            _logger.exception("sidecar dispatch failed for %s %s", method, path)
            status, payload = (
                500,
                {
                    "error": "INTERNAL",
                    "message": "internal server error",
                    "retryable": False,
                },
            )
        self._write_json(status, payload)

    def _read_body(self) -> bytes:
        raw_len = self.headers.get("Content-Length")
        if raw_len is None:
            return b""
        try:
            length = int(raw_len)
        except ValueError:
            return b""
        if length <= 0:
            return b""
        return self.rfile.read(min(length, _READ_CAP))

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        # Close the connection after every response. The handler advertises
        # HTTP/1.1 (keep-alive), but it does not always drain the full request
        # body — an over-_READ_CAP or malformed Content-Length leaves unread
        # bytes on the socket that would desync a reused connection. Signalling
        # ``Connection: close`` (and forcing close_connection) keeps each
        # request/response self-contained.
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002, ANN401 — stdlib sig
        """Route access logs through ``logging`` (the ``print`` rule, #241)."""
        _logger.debug("sidecar %s - %s", self.address_string(), format % args)


def _client_id(headers: dict[str, str], client_address: tuple[str, int]) -> str:
    """Derive a stable rate-limit identity: bearer token if present, else IP.

    Keying on the token isolates quota per API key; falling back to the source
    address keeps unauthenticated local deployments rate-limited per client.
    """
    auth = ""
    for key, value in headers.items():
        if key.lower() == "authorization":
            auth = value
            break
    parts = auth.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return f"key:{parts[1].strip()}"
    return f"ip:{client_address[0]}"


def make_sidecar_server(
    app: SidecarApp, *, host: str = "127.0.0.1", port: int = 8731
) -> ThreadingHTTPServer:
    """Build (but do not start) a threading HTTP server bound to *app*.

    Tests use this to drive the server on an ephemeral port (``port=0``) and
    call :meth:`~http.server.HTTPServer.handle_request` /
    :meth:`~http.server.HTTPServer.serve_forever` directly.

    Args:
        app: The dispatcher to serve.
        host: Interface to bind (default loopback).
        port: TCP port; ``0`` lets the OS choose an ephemeral port.

    Returns:
        A bound :class:`~http.server.ThreadingHTTPServer`.  Call
        ``serve_forever()`` (typically on a thread) or ``handle_request()``.
    """
    return _SidecarServer((host, port), app)


def serve_api(
    app: SidecarApp, *, host: str = "127.0.0.1", port: int = 8731
) -> None:  # pragma: no cover - blocking loop exercised via examples/CI smoke
    """Serve *app* forever (blocking) over HTTP on *host*:*port*.

    Args:
        app: The dispatcher to serve.
        host: Interface to bind.
        port: TCP port to listen on.
    """
    server = make_sidecar_server(app, host=host, port=port)
    bound_host, bound_port = server.server_address[:2]
    _logger.info("contextweaver sidecar listening on http://%s:%s", bound_host, bound_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # graceful Ctrl-C
        _logger.info("contextweaver sidecar shutting down")
    finally:
        server.server_close()

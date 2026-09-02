"""End-to-end + conformance/load tests for the stdlib HTTP transport (#675/#678).

Drives a real :class:`~http.server.ThreadingHTTPServer` on an ephemeral port
through ``urllib`` — covering the happy path, the typed-error wire shape, and a
small concurrency smoke that asserts the threading server stays correct under
parallel load (the in-process analogue of the non-gating CI load check).
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from typing import Any

import pytest

from contextweaver.adapters._sidecar_http import make_sidecar_server
from contextweaver.adapters.sidecar import SidecarApp, SidecarConfig
from contextweaver.routing.catalog import generate_sample_catalog, load_catalog_dicts
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder


def _build_router() -> Router:
    items = load_catalog_dicts(generate_sample_catalog(n=20, seed=3))
    graph = TreeBuilder().build(items)
    return Router(graph, items=items, top_k=20)


@pytest.fixture
def server() -> Iterator[tuple[str, int]]:
    app = SidecarApp(router=_build_router(), config=SidecarConfig())
    srv: ThreadingHTTPServer = make_sidecar_server(app, host="127.0.0.1", port=0)
    host, port = srv.server_address[:2]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield str(host), int(port)
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def _post(host: str, port: int, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(
        f"http://{host}:{port}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_health_over_http(server: tuple[str, int]) -> None:
    host, port = server
    with urllib.request.urlopen(f"http://{host}:{port}/v1/health", timeout=10) as resp:
        assert resp.status == 200
        assert json.loads(resp.read())["status"] == "ok"


def test_route_over_http(server: tuple[str, int]) -> None:
    host, port = server
    status, body = _post(host, port, "/v1/route", {"query": "send an email", "top_k": 4})
    assert status == 200
    assert body["api_version"] == "v1"
    assert len(body["candidate_ids"]) <= 4


def test_compact_over_http(server: tuple[str, int]) -> None:
    host, port = server
    big = {"rows": [{"i": i, "blob": "y" * 40} for i in range(60)]}
    status, body = _post(host, port, "/v1/compact", {"data": big, "threshold_chars": 100})
    assert status == 200
    assert body["firewalled"] is True


def test_bad_request_error_shape_over_http(server: tuple[str, int]) -> None:
    host, port = server
    status, body = _post(host, port, "/v1/route", {"top_k": 3})
    assert status == 400
    assert body["error"] == "BAD_REQUEST"
    assert "retryable" in body


def test_response_closes_connection(server: tuple[str, int]) -> None:
    # The handler does not always drain the request body, so it must not keep the
    # connection alive — every response advertises ``Connection: close``.
    host, port = server
    with urllib.request.urlopen(f"http://{host}:{port}/v1/health", timeout=10) as resp:
        assert resp.status == 200
        assert resp.headers.get("Connection", "").lower() == "close"


#: Simultaneous fresh connections the concurrency smoke opens.
#:
#: This is a connection burst, not just parallel requests: every response
#: carries ``Connection: close``, so each of these is its own TCP connect.
#: What it proves is that the threading server accepts and answers a burst
#: larger than ``socketserver``'s default backlog of 5 — the shape a client
#: fanning out parallel tool calls produces (#835).
_BURST = 20


def test_concurrent_requests_stay_correct(server: tuple[str, int]) -> None:
    host, port = server

    # The listening socket exists before ``serve_forever`` starts, so a connect
    # can land in the accept queue while nothing is accepting yet. Probe once
    # so the burst measures the server under load, not its startup race.
    with urllib.request.urlopen(f"http://{host}:{port}/v1/health", timeout=10) as resp:
        assert resp.status == 200

    outcomes: list[tuple[int, str]] = []
    lock = threading.Lock()

    def worker(index: int) -> None:
        # Every worker records an outcome, including a transport failure.
        # Losing the exception to a dead thread is what turned a single
        # ``ConnectionResetError`` into an unattributable ``len(results) < 20``
        # and a whole-matrix rerun (#835). Nothing is swallowed: an entry that
        # is not ``200`` fails the assertion below, carrying its own diagnosis.
        try:
            status, _ = _post(host, port, "/v1/route", {"query": "lookup a record", "top_k": 3})
            result = str(status)
        except Exception as exc:  # noqa: BLE001 - reported, never suppressed
            result = f"{type(exc).__name__}: {exc}"
        with lock:
            outcomes.append((index, result))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(_BURST)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    stalled = [i for i, thread in enumerate(threads) if thread.is_alive()]
    failures = [entry for entry in sorted(outcomes) if entry[1] != "200"]
    assert not stalled, f"workers still running after join: {stalled}"
    assert not failures, f"non-200 outcomes: {failures}"
    assert len(outcomes) == _BURST, f"missing outcomes, got {sorted(outcomes)}"


def test_server_backlog_absorbs_the_burst() -> None:
    """The accept queue must be at least as deep as the burst above.

    ``socketserver.TCPServer`` defaults ``request_queue_size`` to 5. With 20
    simultaneous connects that overflows, and the kernel answers the excess
    with a reset the client reports as ``ConnectionResetError`` — no server
    log, no failed request, just a transport error that reruns "fix" (#835).
    Guarding the number here means the concurrency smoke cannot start passing
    for the wrong reason if the burst is ever raised.
    """
    app = SidecarApp(router=_build_router(), config=SidecarConfig())
    srv = make_sidecar_server(app, host="127.0.0.1", port=0)
    try:
        assert srv.request_queue_size >= _BURST
    finally:
        srv.server_close()

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


def test_concurrent_requests_stay_correct(server: tuple[str, int]) -> None:
    host, port = server
    results: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        status, _ = _post(host, port, "/v1/route", {"query": "lookup a record", "top_k": 3})
        with lock:
            results.append(status)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert len(results) == 20
    assert all(status == 200 for status in results)

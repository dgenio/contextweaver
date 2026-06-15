"""Tests for the transport-free sidecar dispatcher (issues #675 / #676).

Exercises the route/compact happy paths, invalid input, auth rejection, rate
limiting (with an injected clock), the body-size cap, and the
routing-unavailable path — all through :meth:`SidecarApp.dispatch` so no socket
is involved.
"""

from __future__ import annotations

import json
from typing import Any

from contextweaver.adapters.gateway_policy import RateLimit
from contextweaver.adapters.sidecar import SidecarApp, SidecarConfig
from contextweaver.routing.catalog import generate_sample_catalog, load_catalog_dicts
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder


def _router(n: int = 16) -> Router:
    items = load_catalog_dicts(generate_sample_catalog(n=n, seed=7))
    graph = TreeBuilder().build(items)
    return Router(graph, items=items, top_k=20)


def _body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode("utf-8")


def test_health_is_open_and_reports_route_state() -> None:
    app = SidecarApp(router=None)
    status, body = app.dispatch("GET", "/v1/health", {}, b"")
    assert status == 200
    assert body["status"] == "ok"
    assert body["route_enabled"] is False


def test_route_happy_path_returns_ranked_candidates_and_cards() -> None:
    app = SidecarApp(router=_router())
    status, body = app.dispatch("POST", "/v1/route", {}, _body({"query": "send email", "top_k": 3}))
    assert status == 200
    assert len(body["candidate_ids"]) <= 3
    assert len(body["scores"]) == len(body["candidate_ids"])
    assert len(body["cards"]) == len(body["candidate_ids"])
    assert body["api_version"] == "v1"


def test_route_top_k_is_a_hard_cap() -> None:
    app = SidecarApp(router=_router())
    _, body = app.dispatch("POST", "/v1/route", {}, _body({"query": "tool", "top_k": 2}))
    assert len(body["candidate_ids"]) <= 2


def test_compact_firewalls_large_payload() -> None:
    app = SidecarApp(router=None)
    big = {"rows": [{"id": i, "blob": "x" * 50} for i in range(60)]}
    status, body = app.dispatch(
        "POST", "/v1/compact", {}, _body({"data": big, "threshold_chars": 100})
    )
    assert status == 200
    assert body["firewalled"] is True
    assert body["tokens_saved"] > 0


def test_compact_passthrough_small_payload() -> None:
    app = SidecarApp(router=None)
    status, body = app.dispatch("POST", "/v1/compact", {}, _body({"data": {"a": 1}}))
    assert status == 200
    assert body["firewalled"] is False
    assert body["tokens_saved"] == 0


def test_invalid_json_is_bad_request() -> None:
    app = SidecarApp(router=_router())
    status, body = app.dispatch("POST", "/v1/route", {}, b"{not json")
    assert status == 400
    assert body["error"] == "BAD_REQUEST"


def test_missing_required_field_is_bad_request() -> None:
    app = SidecarApp(router=_router())
    status, body = app.dispatch("POST", "/v1/route", {}, _body({"top_k": 3}))
    assert status == 400
    assert body["error"] == "BAD_REQUEST"


def test_unknown_path_is_not_found() -> None:
    app = SidecarApp(router=_router())
    status, body = app.dispatch("GET", "/v1/bogus", {}, b"")
    assert status == 404
    assert body["error"] == "NOT_FOUND"


def test_wrong_method_is_method_not_allowed() -> None:
    app = SidecarApp(router=_router())
    status, body = app.dispatch("GET", "/v1/route", {}, b"")
    assert status == 405
    assert body["error"] == "METHOD_NOT_ALLOWED"


def test_route_without_catalog_is_unavailable() -> None:
    app = SidecarApp(router=None)
    status, body = app.dispatch("POST", "/v1/route", {}, _body({"query": "x"}))
    assert status == 503
    assert body["error"] == "ROUTING_UNAVAILABLE"


def test_auth_required_when_api_key_set() -> None:
    app = SidecarApp(router=None, config=SidecarConfig(api_key="secret"))
    status, body = app.dispatch("POST", "/v1/compact", {}, _body({"data": "hi"}))
    assert status == 401
    assert body["error"] == "UNAUTHORIZED"
    # Correct bearer token is accepted.
    headers = {"Authorization": "Bearer secret"}
    status, _ = app.dispatch("POST", "/v1/compact", headers, _body({"data": "hi"}))
    assert status == 200


def test_health_stays_open_under_auth() -> None:
    app = SidecarApp(router=None, config=SidecarConfig(api_key="secret"))
    status, _ = app.dispatch("GET", "/v1/health", {}, b"")
    assert status == 200


def test_rate_limit_per_client_with_injected_clock() -> None:
    clock = [0.0]
    config = SidecarConfig(rate_limit=RateLimit(max_calls_per_minute=2))
    app = SidecarApp(router=None, config=config, clock=lambda: clock[0])
    payload = _body({"data": "hi"})
    assert app.dispatch("POST", "/v1/compact", {}, payload, client_id="a")[0] == 200
    assert app.dispatch("POST", "/v1/compact", {}, payload, client_id="a")[0] == 200
    status, body = app.dispatch("POST", "/v1/compact", {}, payload, client_id="a")
    assert status == 429
    assert body["error"] == "RATE_LIMITED"
    assert body["retryable"] is True
    # A different client has its own quota.
    assert app.dispatch("POST", "/v1/compact", {}, payload, client_id="b")[0] == 200
    # The window slides: 60s later the first client is allowed again.
    clock[0] = 61.0
    assert app.dispatch("POST", "/v1/compact", {}, payload, client_id="a")[0] == 200


def test_body_size_cap_rejects_large_payload() -> None:
    app = SidecarApp(router=None, config=SidecarConfig(max_body_bytes=32))
    status, body = app.dispatch("POST", "/v1/compact", {}, b"x" * 100)
    assert status == 413
    assert body["error"] == "PAYLOAD_TOO_LARGE"


def test_config_round_trip_omits_secret() -> None:
    config = SidecarConfig(api_key="secret", rate_limit=RateLimit(max_calls_per_minute=5))
    out = config.to_dict()
    assert out["auth_required"] is True
    assert "api_key" not in out
    restored = SidecarConfig.from_dict({**out, "rate_limit": {"max_calls_per_minute": 5}})
    assert restored.rate_limit is not None
    assert restored.rate_limit.max_calls_per_minute == 5

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


def test_trailing_slash_routes_like_canonical_path() -> None:
    # ``/v1/route/`` must route identically to ``/v1/route`` rather than 404.
    app = SidecarApp(router=_router())
    status, body = app.dispatch(
        "POST", "/v1/route/", {}, _body({"query": "send email", "top_k": 3})
    )
    assert status == 200
    assert len(body["candidate_ids"]) <= 3
    # Health and compact tolerate a trailing slash too.
    assert app.dispatch("GET", "/v1/health/", {}, b"")[0] == 200
    assert app.dispatch("POST", "/v1/compact/", {}, _body({"data": {"a": 1}}))[0] == 200


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


def test_rate_limit_is_thread_safe_under_concurrency() -> None:
    # The shared SidecarApp guards its per-client RateLimiter with a lock; with a
    # pinned clock the window never slides, so exactly `max_calls_per_minute`
    # concurrent requests from one client must be allowed and the rest rejected.
    # Without the lock the racy increments would over-admit (allowed > limit).
    import threading

    limit = 50
    config = SidecarConfig(rate_limit=RateLimit(max_calls_per_minute=limit))
    app = SidecarApp(router=None, config=config, clock=lambda: 0.0)
    payload = _body({"data": "hi"})
    statuses: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        status, _ = app.dispatch("POST", "/v1/compact", {}, payload, client_id="shared")
        with lock:
            statuses.append(status)

    threads = [threading.Thread(target=worker) for _ in range(limit * 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert len(statuses) == limit * 2
    assert statuses.count(200) == limit
    assert statuses.count(429) == limit


def test_limiter_map_is_bounded_lru() -> None:
    # Per-client limiters are kept as a bounded LRU so a client churning through
    # identities cannot grow the map without limit.
    from contextweaver.adapters.sidecar import _MAX_LIMITERS

    config = SidecarConfig(rate_limit=RateLimit(max_calls_per_minute=5))
    app = SidecarApp(router=None, config=config, clock=lambda: 0.0)
    payload = _body({"data": "hi"})
    for i in range(_MAX_LIMITERS + 10):
        app.dispatch("POST", "/v1/compact", {}, payload, client_id=f"client-{i}")
    assert len(app._limiters) == _MAX_LIMITERS


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


# --- secret scrubbing on /v1/compact (issue #745) ---------------------------

_SC_TOKEN = "sk-ant-" + "api03-" + "zY9xW8vU" * 4
_SC_MASK = "[REDACTED-SECRET]"


def test_compact_scrubs_when_request_opts_in() -> None:
    app = SidecarApp(router=None)
    status, body = app.dispatch(
        "POST",
        "/v1/compact",
        {},
        _body({"data": {"note": f"key {_SC_TOKEN}"}, "redact_secrets": True}),
    )
    assert status == 200
    assert _SC_TOKEN not in json.dumps(body["payload"])
    assert _SC_MASK in json.dumps(body["payload"])


def test_compact_not_scrubbed_by_default() -> None:
    app = SidecarApp(router=None)
    status, body = app.dispatch(
        "POST", "/v1/compact", {}, _body({"data": {"note": f"key {_SC_TOKEN}"}})
    )
    assert status == 200
    assert _SC_TOKEN in json.dumps(body["payload"])


def test_compact_server_default_forces_scrub() -> None:
    # Config redact_secrets=True scrubs even when the request omits the flag.
    app = SidecarApp(router=None, config=SidecarConfig(redact_secrets=True))
    status, body = app.dispatch(
        "POST", "/v1/compact", {}, _body({"data": {"note": f"key {_SC_TOKEN}"}})
    )
    assert status == 200
    assert _SC_TOKEN not in json.dumps(body["payload"])
    assert _SC_MASK in json.dumps(body["payload"])


def test_compact_scrubs_summarizing_branch_over_threshold() -> None:
    app = SidecarApp(router=None)
    big = f"{_SC_TOKEN} " + "words " * 400
    status, body = app.dispatch(
        "POST",
        "/v1/compact",
        {},
        _body({"data": big, "threshold_chars": 100, "redact_secrets": True}),
    )
    assert status == 200
    assert body["firewalled"] is True
    assert _SC_TOKEN not in json.dumps(body["payload"])
    assert _SC_TOKEN not in (body["summary"] or "")

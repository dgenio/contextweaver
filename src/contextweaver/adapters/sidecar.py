"""HTTP sidecar runtime — framework-agnostic dispatch over the two engines.

Runtime layer (issue #675/#676) between the wire contract
(:mod:`contextweaver.adapters.sidecar_contract`) and the stdlib HTTP server
(:mod:`contextweaver.adapters._sidecar_http`).  Transport-free:
:meth:`SidecarApp.dispatch` takes a parsed ``(method, path, headers, body)``
tuple and returns ``(status, json_body)``, so it can be driven by the bundled
server, a test harness, or any WSGI/ASGI shim.  It routes ``POST /v1/route``
over a sync :class:`~contextweaver.routing.router.Router` and ``POST /v1/compact``
through the sync :func:`~contextweaver.context.firewall_api.compact_tool_result`
facade; adds optional bearer-token auth, per-client rate limiting (reusing
:class:`~contextweaver.adapters.gateway_controls.RateLimiter`), a body-size cap,
and typed :class:`~contextweaver.adapters.sidecar_contract.SidecarError`
responses; and never raises across the HTTP boundary.  Both engines are sync, so
the sidecar adds no async surface.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from contextweaver.adapters._sidecar_validation import bearer_token, parse_json_object
from contextweaver.adapters.gateway_controls import RateLimiter
from contextweaver.adapters.gateway_policy import RateLimit, RateLimitPolicy
from contextweaver.adapters.sidecar_contract import (
    SIDECAR_API_VERSION,
    CompactRequest,
    CompactResponse,
    RouteRequest,
    RouteResponse,
    SidecarError,
)
from contextweaver.context.firewall_api import compact_tool_result
from contextweaver.exceptions import ContextWeaverError
from contextweaver.routing.cards import make_choice_cards
from contextweaver.routing.router import Router

#: Bucket name used for the per-client rate limiter (one logical surface).
_RATE_BUCKET = "sidecar"

#: Upper bound on retained per-client rate limiters (LRU-evicted past this), so a
#: client churning identities (rotating IPs/tokens) cannot grow the map unbounded.
_MAX_LIMITERS = 4096

#: HTTP status code per :data:`~contextweaver.adapters.sidecar_contract.SidecarErrorCode`.
_STATUS_BY_CODE: dict[str, int] = {
    "BAD_REQUEST": 400,
    "UNAUTHORIZED": 401,
    "RATE_LIMITED": 429,
    "NOT_FOUND": 404,
    "METHOD_NOT_ALLOWED": 405,
    "PAYLOAD_TOO_LARGE": 413,
    "ROUTING_UNAVAILABLE": 503,
    "INTERNAL": 500,
}


@dataclass
class SidecarConfig:
    """Operator-facing configuration for a :class:`SidecarApp`.

    Attributes:
        api_key: When set, ``/v1/route`` and ``/v1/compact`` require an
            ``Authorization: Bearer <api_key>`` header; ``/v1/health`` stays
            open for liveness probes.  ``None`` disables auth (local use).
        rate_limit: Optional per-client :class:`~contextweaver.adapters.gateway_policy.RateLimit`.
            ``None`` disables rate limiting.
        max_body_bytes: Hard cap on request body size; larger bodies get a
            ``PAYLOAD_TOO_LARGE`` error before parsing.
        deterministic: Forwarded to the firewall facade — when ``True``
            (default) ``/v1/compact`` fails closed rather than calling an LLM.
    """

    api_key: str | None = None
    rate_limit: RateLimit | None = None
    max_body_bytes: int = 1_048_576
    deterministic: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict (``api_key`` is omitted)."""
        out: dict[str, Any] = {
            "max_body_bytes": self.max_body_bytes,
            "deterministic": self.deterministic,
            "auth_required": self.api_key is not None,
        }
        if self.rate_limit is not None:
            out["rate_limit"] = {
                "max_calls_per_minute": self.rate_limit.max_calls_per_minute,
                "max_calls_per_session": self.rate_limit.max_calls_per_session,
            }
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SidecarConfig:
        """Deserialise from a JSON-compatible dict."""
        rate_raw = data.get("rate_limit")
        return cls(
            api_key=data.get("api_key"),
            rate_limit=RateLimit.from_dict(rate_raw) if isinstance(rate_raw, dict) else None,
            max_body_bytes=int(data.get("max_body_bytes", 1_048_576)),
            deterministic=bool(data.get("deterministic", True)),
        )


@dataclass
class SidecarApp:
    """Transport-free dispatcher for the HTTP sidecar surface.

    Build one per process and share it across server threads.  The instance is
    stateless except for per-client rate-limit counters (guarded by an injected
    clock for deterministic tests).

    Args:
        router: Sync router backing ``/v1/route``.  When ``None`` the route
            endpoint returns ``ROUTING_UNAVAILABLE`` (the compaction endpoint
            still works — it needs no catalog).
        config: Operator configuration; defaults to open, unlimited, local use.
        clock: Monotonic clock injected into the rate limiter (tests pin it).
    """

    router: Router | None = None
    config: SidecarConfig = field(default_factory=SidecarConfig)
    clock: Callable[[], float] = time.monotonic
    _limiters: OrderedDict[str, RateLimiter] = field(
        default_factory=OrderedDict, init=False, repr=False
    )
    #: Guards ``_limiters`` + its non-thread-safe ``RateLimiter``s (shared across threads).
    _rate_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def dispatch(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
        *,
        client_id: str = "anonymous",
    ) -> tuple[int, dict[str, Any]]:
        """Route one request to a handler and return ``(status, json_body)``.

        Never raises: engine and parse failures are converted to typed
        :class:`~contextweaver.adapters.sidecar_contract.SidecarError` bodies.
        *client_id* (API key or remote address, supplied by the transport) keys
        the per-client rate limiter.  Returns a JSON-serialisable body dict.
        """
        normalized = {k.lower(): v for k, v in headers.items()}
        # Normalise once so a trailing slash (``/v1/route/``) routes like ``/v1/route``.
        norm_method = method.upper()
        norm_path = path.rstrip("/") or "/"

        if (norm_method, norm_path) == ("GET", "/v1/health"):
            return 200, self._health()

        if norm_path not in ("/v1/route", "/v1/compact"):
            return self._error("NOT_FOUND", f"unknown path {path!r}")
        if norm_method != "POST":
            return self._error("METHOD_NOT_ALLOWED", f"{method} not allowed on {path}")

        auth = self._check_auth(normalized)
        if auth is not None:
            return auth
        if norm_path == "/v1/route" and self.router is None:
            return self._error(
                "ROUTING_UNAVAILABLE",
                "this sidecar was started without a catalog; /v1/route is disabled "
                "(start with `contextweaver serve-api --catalog ...`)",
            )
        limited = self._check_rate_limit(client_id)
        if limited is not None:
            return limited
        if len(body) > self.config.max_body_bytes:
            return self._error(
                "PAYLOAD_TOO_LARGE",
                f"request body exceeds {self.config.max_body_bytes} bytes",
            )

        try:
            payload = parse_json_object(body)
            if norm_path == "/v1/route":
                return 200, self._route(payload).to_dict()
            return 200, self._compact(payload).to_dict()
        except ContextWeaverError as exc:
            # ConfigError + other engine failures (e.g. empty catalog) are the
            # client's request hitting a limit — surface as 400, not 500.
            return self._error("BAD_REQUEST", str(exc))

    # -- handlers -------------------------------------------------------------

    def _route(self, payload: dict[str, Any]) -> RouteResponse:
        # narrow: ``dispatch`` returns ROUTING_UNAVAILABLE when ``router`` is
        # None, so it is never None here — assert only narrows the type.
        assert self.router is not None  # noqa: S101  # narrow type for mypy
        req = RouteRequest.from_dict(payload)
        result = self.router.route(
            req.query,
            exclude_ids=set(req.exclude_ids) or None,
            allowed_namespaces=set(req.allowed_namespaces) or None,
            context_hints=req.context_hints or None,
        )
        # ``top_k`` is a per-request cap on top of the server's routing ceiling
        # (the Router's configured ``top_k``); truncate the ranked tail.
        ids = list(result.candidate_ids[: req.top_k])
        scores = list(result.scores[: req.top_k])
        items = list(result.candidate_items[: req.top_k])
        cards = make_choice_cards(
            items,
            max_cards=req.top_k,
            scores=dict(zip(ids, scores, strict=False)),
        )
        return RouteResponse(
            candidate_ids=ids,
            scores=scores,
            is_ambiguous=result.is_ambiguous,
            clarifying_question=result.clarifying_question,
            cards=[card.to_dict() for card in cards],
        )

    def _compact(self, payload: dict[str, Any]) -> CompactResponse:
        req = CompactRequest.from_dict(payload)
        out = compact_tool_result(
            req.data,
            threshold_chars=req.threshold_chars,
            budget=req.budget,
            strategy=req.strategy,
            keep=req.keep or None,
            deterministic=self.config.deterministic,
        )
        saved = max(0, out.stats.original_tokens - out.stats.summary_tokens)
        return CompactResponse(
            firewalled=out.firewalled,
            payload=out.payload,
            summary=out.summary,
            facts=list(out.facts),
            artifact_ref=out.artifact_ref,
            tokens_saved=saved,
        )

    def _health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "api_version": SIDECAR_API_VERSION,
            "route_enabled": self.router is not None,
        }

    # -- auth + rate limit ----------------------------------------------------

    def _check_auth(self, headers: dict[str, str]) -> tuple[int, dict[str, Any]] | None:
        if self.config.api_key is None:
            return None
        provided = bearer_token(headers.get("authorization", ""))
        # Constant-time compare (no timing side-channel); None guard first.
        if provided is None or not secrets.compare_digest(provided, self.config.api_key):
            return self._error("UNAUTHORIZED", "missing or invalid bearer token")
        return None

    def _check_rate_limit(self, client_id: str) -> tuple[int, dict[str, Any]] | None:
        if self.config.rate_limit is None:
            return None
        # ``RateLimiter`` mutates per-bucket deques/dicts and is not thread-safe;
        # the app is shared across server threads, so both the per-client limiter
        # lookup/creation and the ``check`` call must happen under the lock.
        with self._rate_lock:
            limiter = self._limiters.get(client_id)
            if limiter is None:
                policy = RateLimitPolicy(per_meta_tool={_RATE_BUCKET: self.config.rate_limit})
                limiter = RateLimiter(policy, clock=self.clock)
                self._limiters[client_id] = limiter
                if len(self._limiters) > _MAX_LIMITERS:
                    self._limiters.popitem(last=False)  # evict least-recently-seen
            else:
                self._limiters.move_to_end(client_id)
            decision = limiter.check(_RATE_BUCKET)
        if decision.allowed:
            return None
        details: dict[str, Any] = {"scope": decision.scope}
        if decision.retry_after is not None:
            details["retry_after"] = round(decision.retry_after, 3)
        return self._error("RATE_LIMITED", "rate limit exceeded", retryable=True, details=details)

    def _error(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        err = SidecarError(
            code=code,  # type: ignore[arg-type]  # validated by _STATUS_BY_CODE below
            message=message,
            retryable=retryable,
            details=details or {},
        )
        return _STATUS_BY_CODE[code], err.to_dict()

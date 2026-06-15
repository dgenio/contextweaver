"""Wire contract for the HTTP sidecar — versioned request/response/error shapes.

The sidecar (issue #427, decomposed into #674/#675/#676/#677/#678) exposes the
two highest-value engines over a small, versioned HTTP/JSON API so non-Python
agents can use contextweaver without embedding Python: ``POST /v1/route``
(tool routing) and ``POST /v1/compact`` (tool-result compaction).

This is the *contract* layer (issue #674): pure, dependency-free dataclasses
with ``to_dict`` / ``from_dict`` mirroring every other public result type in the
repo, plus the JSON-Schema documents published under ``schemas/sidecar/v1/``.
It imports no HTTP machinery.  Errors travel on the wire as :class:`SidecarError`,
mirroring the ``docs/gateway_spec.md`` §3.4 shape used by
:class:`~contextweaver.adapters.gateway_error.GatewayError` so one client error
model covers both surfaces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from contextweaver.exceptions import ConfigError

#: Current sidecar API version.  Bumped only on a breaking wire change; the
#: path prefix (``/v1``) and every payload's ``api_version`` field carry it so
#: clients can pin and detect drift.
SIDECAR_API_VERSION: str = "v1"

#: Routing strategy values accepted on ``/v1/compact`` (mirrors the firewall
#: facade :data:`contextweaver.context.firewall_api.Strategy`).
CompactStrategy = Literal["auto", "structured", "text", "passthrough"]

#: Stable error codes returned on the wire.  ``BAD_REQUEST`` covers malformed
#: payloads (a :class:`~contextweaver.exceptions.ConfigError` from the engines
#: maps here); the rest are sidecar-level transport conditions.
SidecarErrorCode = Literal[
    "BAD_REQUEST",
    "UNAUTHORIZED",
    "RATE_LIMITED",
    "NOT_FOUND",
    "METHOD_NOT_ALLOWED",
    "PAYLOAD_TOO_LARGE",
    "ROUTING_UNAVAILABLE",
    "INTERNAL",
]


def _require_str(payload: dict[str, Any], key: str) -> str:
    """Return ``payload[key]`` as a non-empty ``str`` or raise ``ConfigError``."""
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"sidecar request field {key!r} must be a non-empty string")
    return value


def _opt_int(payload: dict[str, Any], key: str, default: int) -> int:
    """Return ``payload[key]`` coerced to ``int`` (default when absent/null)."""
    value = payload.get(key, default)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"sidecar request field {key!r} must be an integer")
    return int(value)


def _opt_str_list(payload: dict[str, Any], key: str) -> list[str]:
    """Return ``payload[key]`` as a ``list[str]`` (empty when absent/null)."""
    value = payload.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"sidecar request field {key!r} must be a list of strings")
    return list(value)


@dataclass
class RouteRequest:
    """Request body for ``POST /v1/route``.

    Attributes:
        query: The user query string to route (required, non-empty).
        top_k: Maximum number of ranked candidates to return.  This is a
            per-request cap on top of the server's configured routing ceiling
            (set at ``serve-api`` time); requests cannot raise the ceiling.
        exclude_ids: Item IDs to drop before scoring (negative routing, #112).
        allowed_namespaces: Namespace allow-list for toolset gating (#22).
        context_hints: Conversation hints appended to the scoring query (#116).
    """

    query: str
    top_k: int = 10
    exclude_ids: list[str] = field(default_factory=list)
    allowed_namespaces: list[str] = field(default_factory=list)
    context_hints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "query": self.query,
            "top_k": self.top_k,
            "exclude_ids": list(self.exclude_ids),
            "allowed_namespaces": list(self.allowed_namespaces),
            "context_hints": list(self.context_hints),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RouteRequest:
        """Validate and parse a request body.

        Raises:
            ConfigError: If a field is missing or has the wrong type.  The
                sidecar maps this to a ``BAD_REQUEST`` wire error.
        """
        if not isinstance(data, dict):
            raise ConfigError("sidecar route request body must be a JSON object")
        top_k = _opt_int(data, "top_k", 10)
        if top_k < 1:
            raise ConfigError("sidecar route request field 'top_k' must be >= 1")
        return cls(
            query=_require_str(data, "query"),
            top_k=top_k,
            exclude_ids=_opt_str_list(data, "exclude_ids"),
            allowed_namespaces=_opt_str_list(data, "allowed_namespaces"),
            context_hints=_opt_str_list(data, "context_hints"),
        )


@dataclass
class RouteResponse:
    """Response body for ``POST /v1/route``.

    Attributes:
        candidate_ids: Ranked candidate item IDs (at most ``top_k``).
        scores: Score per candidate, same order as ``candidate_ids``.
        is_ambiguous: ``True`` when the rank-1/rank-2 gap is below threshold.
        clarifying_question: Optional disambiguation prompt when ambiguous.
        cards: LLM-friendly :class:`~contextweaver.envelope.ChoiceCard` dicts
            (never full schemas) for the ranked candidates.
        api_version: Echoes :data:`SIDECAR_API_VERSION`.
    """

    candidate_ids: list[str] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)
    is_ambiguous: bool = False
    clarifying_question: str | None = None
    cards: list[dict[str, Any]] = field(default_factory=list)
    api_version: str = SIDECAR_API_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "api_version": self.api_version,
            "candidate_ids": list(self.candidate_ids),
            "scores": [float(s) for s in self.scores],
            "is_ambiguous": self.is_ambiguous,
            "clarifying_question": self.clarifying_question,
            "cards": [dict(c) for c in self.cards],
        }


@dataclass
class CompactRequest:
    """Request body for ``POST /v1/compact``.

    Attributes:
        data: The tool result to compact — a JSON object, array, or string.
        threshold_chars: Payloads at or below this size pass through unchanged.
        budget: Soft token budget for the inline text summary.
        strategy: Firewall strategy (see :data:`CompactStrategy`).
        keep: JSON-path allow-list for the structured projection strategy.
    """

    data: dict[str, Any] | list[Any] | str
    threshold_chars: int = 2000
    budget: int = 800
    strategy: CompactStrategy = "auto"
    keep: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "data": self.data,
            "threshold_chars": self.threshold_chars,
            "budget": self.budget,
            "strategy": self.strategy,
            "keep": list(self.keep),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompactRequest:
        """Validate and parse a request body.

        Raises:
            ConfigError: If a field is missing or has the wrong type.
        """
        if not isinstance(data, dict):
            raise ConfigError("sidecar compact request body must be a JSON object")
        if "data" not in data:
            raise ConfigError("sidecar compact request field 'data' is required")
        payload = data["data"]
        if not isinstance(payload, (dict, list, str)):
            raise ConfigError(
                "sidecar compact request field 'data' must be a JSON object, array, or string"
            )
        strategy = data.get("strategy", "auto")
        if strategy not in ("auto", "structured", "text", "passthrough"):
            raise ConfigError(
                "sidecar compact request field 'strategy' must be one of "
                "'auto', 'structured', 'text', 'passthrough'"
            )
        threshold_chars = _opt_int(data, "threshold_chars", 2000)
        budget = _opt_int(data, "budget", 800)
        if threshold_chars < 0:
            raise ConfigError("sidecar compact request field 'threshold_chars' must be >= 0")
        return cls(
            data=payload,
            threshold_chars=threshold_chars,
            budget=budget,
            strategy=strategy,
            keep=_opt_str_list(data, "keep"),
        )


@dataclass
class CompactResponse:
    """Response body for ``POST /v1/compact``.

    Attributes:
        firewalled: ``True`` when the payload was offloaded out-of-band.
        payload: The object to hand to the LLM (shape-preserving pass-through
            below threshold, projected/summary envelope when firewalled).
        summary: Inline summary text, or ``None`` on pass-through.
        facts: Structured facts derived from the payload (may be empty).
        artifact_ref: Handle of the offloaded raw payload, or ``None``.
        tokens_saved: Tokens kept out of the prompt (``original − summary``).
        api_version: Echoes :data:`SIDECAR_API_VERSION`.
    """

    firewalled: bool
    payload: Any
    summary: str | None = None
    facts: list[str] = field(default_factory=list)
    artifact_ref: str | None = None
    tokens_saved: int = 0
    api_version: str = SIDECAR_API_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "api_version": self.api_version,
            "firewalled": self.firewalled,
            "payload": self.payload,
            "summary": self.summary,
            "facts": list(self.facts),
            "artifact_ref": self.artifact_ref,
            "tokens_saved": self.tokens_saved,
        }


@dataclass
class SidecarError:
    """Structured wire error mirroring ``docs/gateway_spec.md`` §3.4.

    Attributes:
        code: One of :data:`SidecarErrorCode`.
        message: Short, human-readable description (control-char free).
        retryable: Hint that the client may retry the same call.
        details: Optional implementation-defined diagnostics.
    """

    code: SidecarErrorCode
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the §3.4 JSON shape (``error`` carries the code)."""
        out: dict[str, Any] = {
            "error": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details:
            out["details"] = dict(self.details)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SidecarError:
        """Deserialise from the §3.4 JSON shape."""
        return cls(
            code=data["error"],
            message=data.get("message", ""),
            retryable=bool(data.get("retryable", False)),
            details=dict(data.get("details", {})),
        )

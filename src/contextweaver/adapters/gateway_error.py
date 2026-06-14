"""Structured error payload for the MCP proxy/gateway meta-tools.

The :class:`GatewayError` dataclass is the on-the-wire error shape
specified by ``docs/gateway_spec.md`` §3.4.  It is returned from
``tool_browse``, ``tool_execute``, ``tool_view``, and ``tool_hydrate``
when an invocation cannot be satisfied — the meta-tools never raise
across the MCP boundary.

A structured upstream-error taxonomy (issue #485) distinguishes failures
agents recover from differently — a timeout invites retry, an auth failure
invites escalation — via :func:`classify_upstream_exception`.  Upstream
exception text (which can carry hostnames, paths, or tokens) is passed
through :func:`redact_upstream_detail` before it reaches model-visible
context; operators keep the full detail via server-side logging.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Literal

GatewayErrorCode = Literal[
    "PATH_INVALID",
    "PATH_NOT_FOUND",
    "ARGS_INVALID",
    "SCHEMA_INVALID",
    "UPSTREAM_ERROR",
    "UPSTREAM_TIMEOUT",
    "UPSTREAM_UNAVAILABLE",
    "AUTH_FAILED",
    "PERMISSION_DENIED",
    "RATE_LIMITED",
    "HYDRATE_FAILED",
    "VIEW_FAILED",
    "RESOURCE_NOT_FOUND",
    "PROMPT_NOT_FOUND",
]

#: Default cap on the length of model-visible upstream error detail.
DEFAULT_DETAIL_MAX_LEN = 256

# Matches C0/C1 control characters (including newlines and tabs) so redacted
# detail is a single clean line with no terminal-escape or layout-breaking bytes.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


@dataclass
class GatewayError:
    """Structured error payload returned from a gateway/proxy meta-tool.

    Attributes:
        code: One of the gateway error codes (§3.4).  ``UPSTREAM_ERROR`` and the
            finer-grained ``UPSTREAM_TIMEOUT`` / ``UPSTREAM_UNAVAILABLE`` /
            ``AUTH_FAILED`` / ``PERMISSION_DENIED`` / ``RATE_LIMITED`` codes
            classify failures from the wrapped MCP server (issue #485);
            ``SCHEMA_INVALID`` flags an upstream tool schema that failed
            ingest-time validation (issue #484); ``HYDRATE_FAILED`` and
            ``VIEW_FAILED`` cover the gateway's other two meta-tools.
        message: A short, human-readable description of the failure.  For
            upstream failures this is redacted detail safe for model context.
        path: The offending path or tool_id when relevant; empty string
            otherwise.
        retryable: Hint that a client may retry the same call (e.g. timeouts,
            transient unavailability, rate limits).  Lets agent loops branch
            without string-matching the message.
        details: Optional implementation-defined diagnostics (e.g.
            ``jsonschema`` validation error path lists).
    """

    code: GatewayErrorCode
    message: str
    path: str = ""
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the §3.4 JSON shape."""
        out: dict[str, Any] = {
            "error": self.code,
            "message": self.message,
            "path": self.path,
            "retryable": self.retryable,
        }
        if self.details:
            out["details"] = dict(self.details)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GatewayError:
        """Deserialise from the §3.4 JSON shape."""
        return cls(
            code=data["error"],
            message=data.get("message", ""),
            path=data.get("path", ""),
            retryable=bool(data.get("retryable", False)),
            details=dict(data.get("details", {})),
        )


def redact_upstream_detail(text: str, *, max_len: int = DEFAULT_DETAIL_MAX_LEN) -> str:
    """Return *text* stripped of control characters and capped in length (#485).

    Upstream exception text can contain hostnames, filesystem paths, tokens, or
    terminal-escape bytes.  This collapses it to a single bounded line safe for
    model-visible context; operators retain the full detail via logging.

    Note: this **sanitises and bounds** the text — it strips control characters
    and truncates to *max_len* — it does **not** scrub secrets.  Secrets are not
    detectable in arbitrary upstream text; the length cap bounds the exposure
    blast radius, and full detail is kept operator-side only (never the model).

    Args:
        text: Raw upstream error detail.
        max_len: Maximum length of the returned string.

    Returns:
        A single-line, length-bounded, control-character-free string.
    """
    cleaned = " ".join(_CONTROL_RE.sub(" ", text).split())
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1].rstrip() + "…"
    return cleaned


def classify_upstream_exception(exc: BaseException) -> tuple[GatewayErrorCode, bool]:
    """Map an upstream-call exception to a taxonomy code + retryable hint (#485).

    The mapping is intentionally conservative: well-understood exception types
    (timeouts, connection failures) map structurally, a small set of
    auth/permission/rate signatures map by message, and everything else falls
    back to the generic ``UPSTREAM_ERROR``.

    Args:
        exc: The exception raised by the upstream ``call_tool`` invocation.

    Returns:
        ``(code, retryable)``.
    """
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "UPSTREAM_TIMEOUT", True
    if isinstance(exc, ConnectionError):
        return "UPSTREAM_UNAVAILABLE", True

    text = str(exc).lower()
    if any(
        marker in text for marker in ("unauthorized", "401", "authentication", "invalid api key")
    ):
        return "AUTH_FAILED", False
    if any(marker in text for marker in ("forbidden", "403", "permission denied", "access denied")):
        return "PERMISSION_DENIED", False
    if any(marker in text for marker in ("rate limit", "429", "too many requests")):
        return "RATE_LIMITED", True
    if "timed out" in text or "timeout" in text:
        return "UPSTREAM_TIMEOUT", True
    return "UPSTREAM_ERROR", False

"""Structured error payload for the MCP proxy/gateway meta-tools.

The :class:`GatewayError` dataclass is the on-the-wire error shape
specified by ``docs/gateway_spec.md`` §3.4.  It is returned from
``tool_browse``, ``tool_execute``, ``tool_view``, and ``tool_hydrate``
when an invocation cannot be satisfied — the meta-tools never raise
across the MCP boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

GatewayErrorCode = Literal[
    "PATH_INVALID",
    "PATH_NOT_FOUND",
    "ARGS_INVALID",
    "UPSTREAM_ERROR",
    "HYDRATE_FAILED",
    "VIEW_FAILED",
]


@dataclass
class GatewayError:
    """Structured error payload returned from a gateway/proxy meta-tool.

    Attributes:
        code: One of the gateway error codes (§3.4).  ``UPSTREAM_ERROR``
            extends the spec list to cover transport failures from the
            wrapped MCP server; ``HYDRATE_FAILED`` and ``VIEW_FAILED``
            cover the analogous failures on the gateway's other two
            meta-tools.
        message: A short, human-readable description of the failure.
        path: The offending path or tool_id when relevant; empty string
            otherwise.
        details: Optional implementation-defined diagnostics (e.g.
            ``jsonschema`` validation error path lists).
    """

    code: GatewayErrorCode
    message: str
    path: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the §3.4 JSON shape."""
        out: dict[str, Any] = {
            "error": self.code,
            "message": self.message,
            "path": self.path,
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
            details=dict(data.get("details", {})),
        )

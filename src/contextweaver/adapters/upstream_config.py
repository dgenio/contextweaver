"""Pure-data configuration for one live upstream MCP server (#366/#368).

Config shape (nested under ``upstreams.<name>`` in ``mcp serve --config``)::

    upstreams:
      filesystem:
        type: stdio
        command: npx
        args: ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
        namespace: fs
        required: true
        include_tools: ["read_*"]
        exclude_tools: ["delete_*"]
      github:
        type: http
        url: "https://example.com/mcp"
        headers:
          Authorization: "Bearer ${env:GITHUB_TOKEN}"
        namespace: github
        required: false

``${env:VAR}`` placeholders in ``command``, ``args``, ``env`` values, ``url``,
and ``headers`` values are interpolated from the process environment at parse
time; an unset variable is a configuration error (fail loud, not empty-string).

The matching *behaviour* (connecting, launching, namespace/filter wrapping)
lives in :mod:`contextweaver.adapters.upstream_launch`; the fault-tolerant
startup policy lives in :mod:`contextweaver.adapters.startup_policy`; the
artifact-lifecycle policy lives in :mod:`contextweaver.adapters.artifact_policy`.
Splitting along these lines mirrors the existing ``gateway_policy`` /
``gateway_controls`` config-vs-behaviour split and keeps each module small.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any, ClassVar

from contextweaver.adapters._config_coerce import coerce_bool, interpolate_env, str_map, str_tuple
from contextweaver.exceptions import ConfigError

#: Supported upstream transport types.
UPSTREAM_TYPES: frozenset[str] = frozenset({"stdio", "http", "sse"})

_UPSTREAM_SPEC_KEYS: frozenset[str] = frozenset(
    {
        "type",
        "command",
        "args",
        "env",
        "url",
        "headers",
        "namespace",
        "required",
        "include_tools",
        "exclude_tools",
        "timeout",
    }
)


@dataclass(frozen=True)
class UpstreamSpec:
    """One configured upstream MCP server (#366/#368).

    Attributes:
        name: The config-block key identifying this upstream (used in
            diagnostics and status reports).
        type: Transport: ``"stdio"`` (launch a child process), ``"http"``
            (streamable HTTP, the current MCP-spec-recommended transport for
            already-running servers), or ``"sse"`` (legacy; kept for servers
            that have not migrated off SSE).
        command: Executable for ``type="stdio"``.
        args: Command-line arguments for ``type="stdio"``.
        env: Extra environment variables for the child process
            (``type="stdio"``); values may use ``${env:VAR}`` placeholders.
        url: Endpoint URL for ``type="http"`` / ``type="sse"``.
        headers: HTTP headers for ``type="http"`` / ``type="sse"``; values
            may use ``${env:VAR}`` placeholders (e.g. bearer tokens).
        namespace: Optional dotted prefix applied to every tool name this
            upstream exports (e.g. ``"github"`` turns ``create_issue`` into
            ``github.create_issue``), so the existing canonical-``tool_id``
            namespace inference (:func:`contextweaver.adapters.mcp.infer_namespace`)
            picks it up with no further wiring. Empty string (default) leaves
            names untouched.
        required: Whether a failure to start this upstream aborts the whole
            gateway under ``startup.mode="strict"`` (#374). Non-required
            upstreams that fail are simply reported and excluded.
        include_tools: Glob patterns (matched against the upstream's own,
            pre-namespace tool names); when non-empty, only matching tools
            are exposed. Evaluated before :attr:`exclude_tools`.
        exclude_tools: Glob patterns; matching tools are always excluded,
            even if they also match :attr:`include_tools`.
        timeout: Per-call timeout in seconds forwarded to
            :class:`~contextweaver.adapters.mcp_upstream.McpClientUpstream`.
    """

    name: str
    type: str = "stdio"
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    namespace: str = ""
    required: bool = True
    include_tools: tuple[str, ...] = ()
    exclude_tools: tuple[str, ...] = ()
    timeout: float = 30.0

    _RESERVED_TYPE: ClassVar[frozenset[str]] = UPSTREAM_TYPES

    def __post_init__(self) -> None:
        if not self.name:
            raise ConfigError("upstream name must be non-empty")
        if self.type not in self._RESERVED_TYPE:
            allowed = ", ".join(sorted(self._RESERVED_TYPE))
            raise ConfigError(f"upstream {self.name!r}: type must be one of {allowed}")
        if self.type == "stdio" and not self.command:
            raise ConfigError(f"upstream {self.name!r}: type 'stdio' requires 'command'")
        if self.type in ("http", "sse") and not self.url:
            raise ConfigError(f"upstream {self.name!r}: type {self.type!r} requires 'url'")
        if self.timeout <= 0:
            raise ConfigError(f"upstream {self.name!r}: timeout must be positive")

    def matches_tool(self, upstream_tool_name: str) -> bool:
        """Return whether *upstream_tool_name* passes the include/exclude filters.

        Filters are evaluated against the upstream's own (pre-namespace)
        name, so patterns like ``"read_*"`` describe what the upstream calls
        its tools, not the namespaced id contextweaver later derives.
        """
        if any(fnmatch.fnmatchcase(upstream_tool_name, p) for p in self.exclude_tools):
            return False
        if self.include_tools:
            return any(fnmatch.fnmatchcase(upstream_tool_name, p) for p in self.include_tools)
        return True

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "type": self.type,
            "command": self.command,
            "args": list(self.args),
            "env": dict(self.env),
            "url": self.url,
            "headers": dict(self.headers),
            "namespace": self.namespace,
            "required": self.required,
            "include_tools": list(self.include_tools),
            "exclude_tools": list(self.exclude_tools),
            "timeout": self.timeout,
        }

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> UpstreamSpec:
        """Build one upstream spec from its ``upstreams.<name>`` config block."""
        if not isinstance(data, dict):
            raise ConfigError(f"upstream {name!r} config must be a mapping")
        unknown = sorted(set(data) - _UPSTREAM_SPEC_KEYS)
        if unknown:
            allowed = ", ".join(sorted(_UPSTREAM_SPEC_KEYS))
            raise ConfigError(f"upstream {name!r}: unknown key(s) {unknown}; allowed: {allowed}")
        command = data.get("command")
        if command is not None:
            command = interpolate_env(str(command))
        url = data.get("url")
        if url is not None:
            url = interpolate_env(str(url))
        raw_args = str_tuple(f"upstream {name!r}.args", data.get("args"))
        return cls(
            name=name,
            type=str(data.get("type", "stdio")),
            command=command,
            args=tuple(interpolate_env(a) for a in raw_args),
            env=str_map(f"upstream {name!r}.env", data.get("env"), interpolate=True),
            url=url,
            headers=str_map(f"upstream {name!r}.headers", data.get("headers"), interpolate=True),
            namespace=str(data.get("namespace", "")),
            required=coerce_bool(f"upstream {name!r}.required", data.get("required"), True),
            include_tools=str_tuple(f"upstream {name!r}.include_tools", data.get("include_tools")),
            exclude_tools=str_tuple(f"upstream {name!r}.exclude_tools", data.get("exclude_tools")),
            timeout=float(data.get("timeout", 30.0)),
        )


def parse_upstreams_config(raw: dict[str, Any]) -> list[UpstreamSpec]:
    """Parse the ``upstreams`` config block into a list of :class:`UpstreamSpec`.

    Returned in the order the mapping was declared (dict insertion order),
    which becomes the deterministic first-registered-wins collision order.
    """
    if not isinstance(raw, dict):
        raise ConfigError("upstreams config must be a mapping of name -> spec")
    if not raw:
        raise ConfigError("upstreams config must declare at least one upstream")
    return [UpstreamSpec.from_dict(str(name), spec) for name, spec in raw.items()]


__all__ = [
    "UPSTREAM_TYPES",
    "UpstreamSpec",
    "parse_upstreams_config",
]

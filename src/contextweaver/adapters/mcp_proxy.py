"""Transparent MCP proxy (#13) — stripped ``tools/list`` + invocation channel.

The transparent proxy publishes two surfaces per
``docs/gateway_spec.md`` §4.1:

1. **Discovery channel** — :func:`make_stripped_tools_list` replaces the
   upstream ``inputSchema`` with the sentinel ``{"type": "object"}`` for
   every tool, keeping prompt cost constant per tool.
2. **Invocation channel** — :func:`make_proxy_meta_tools` exposes two
   meta-tools, ``tool_hydrate(tool_id)`` and ``tool_execute(tool_id, args)``,
   so an agent can retrieve a real schema only when it intends to call
   the underlying tool.

Both surfaces dispatch through :class:`~contextweaver.adapters.proxy_runtime.ProxyRuntime`
in :attr:`ExposureMode.TRANSPARENT` mode.

A note on MCP ``name`` characters: the canonical ``tool_id`` may contain
``:`` and ``#``.  MCP treats ``name`` as opaque, but strict downstream
clients may reject these characters.  Implementations MAY URL-encode the
``tool_id`` in the ``name`` field; round-trip is preserved because the
canonical helpers (:func:`~contextweaver.routing.tool_id.parse_tool_id` /
:func:`~contextweaver.routing.tool_id.format_tool_id`) consume the
decoded form.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from contextweaver.adapters.gateway_error import GatewayError
from contextweaver.adapters.mcp_gateway import envelope_call_result
from contextweaver.adapters.proxy_runtime import ExposureMode, ProxyRuntime

logger = logging.getLogger("contextweaver.adapters.mcp_proxy")

TOOL_HYDRATE = "tool_hydrate"
TOOL_EXECUTE = "tool_execute"

PROXY_META_TOOL_NAMES = (TOOL_HYDRATE, TOOL_EXECUTE)


def make_stripped_tools_list(runtime: ProxyRuntime) -> list[dict[str, Any]]:
    """Return the stripped ``tools/list`` for the transparent-proxy mode (§4.1).

    The returned list mirrors :meth:`ProxyRuntime.strip_tools_list` and
    appends the two meta-tools published on the invocation channel so a
    single ``tools/list`` response covers both surfaces.

    Args:
        runtime: A configured :class:`ProxyRuntime`.  ``runtime.mode``
            is not enforced because the proxy may be composed with a
            gateway in the same process during migration; the caller is
            responsible for matching mode to wiring.

    Returns:
        A list of MCP-format tool definitions: ``len(catalog) + 2``
        entries.
    """
    entries = list(runtime.strip_tools_list())
    entries.extend(make_proxy_meta_tools(runtime))
    return entries


def make_proxy_meta_tools(runtime: ProxyRuntime) -> list[dict[str, Any]]:
    """Return the two invocation-channel meta-tools (§4.1)."""
    _ = runtime
    return [
        {
            "name": TOOL_HYDRATE,
            "description": (
                "Retrieve the full input schema for a tool by its canonical "
                "tool_id.  Use this before tool_execute to inspect the "
                "schema you must supply."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"tool_id": {"type": "string"}},
                "required": ["tool_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": TOOL_EXECUTE,
            "description": (
                "Invoke an upstream tool by canonical tool_id.  Arguments "
                "are validated against the hydrated input schema before "
                "dispatch."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tool_id": {"type": "string"},
                    "args": {"type": "object"},
                },
                "required": ["tool_id", "args"],
                "additionalProperties": False,
            },
        },
    ]


async def dispatch_proxy_request(
    runtime: ProxyRuntime,
    method: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Route an MCP request to the proxy's discovery or invocation channel.

    Supports the two MCP methods relevant to the proxy:

    - ``tools/list`` → returns the stripped catalog (§4.1).
    - ``tools/call`` → routes to ``tool_hydrate`` or ``tool_execute``
      based on the ``name`` field.

    Args:
        runtime: A :class:`ProxyRuntime` (typically in
            :attr:`ExposureMode.TRANSPARENT` mode).
        method: The MCP method name.
        params: The MCP request params (already JSON-decoded).

    Returns:
        An MCP-format response dict.  Errors are returned as
        ``isError=True`` ``CallToolResult`` payloads to match how MCP
        clients consume tool failures.
    """
    if method == "tools/list":
        return {"tools": make_stripped_tools_list(runtime)}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {}) or {}
        if not isinstance(name, str):
            return envelope_call_result(
                GatewayError(
                    code="ARGS_INVALID",
                    message="tools/call params require a string 'name'.",
                ),
                label="tools/call",
            )
        if name == TOOL_HYDRATE:
            tool_id = args.get("tool_id")
            if not isinstance(tool_id, str):
                return envelope_call_result(
                    GatewayError(
                        code="ARGS_INVALID",
                        message="tool_hydrate requires a string 'tool_id'.",
                    ),
                    label=TOOL_HYDRATE,
                )
            hydrated = runtime.hydrate(tool_id)
            if isinstance(hydrated, GatewayError):
                return envelope_call_result(hydrated, label=TOOL_HYDRATE)
            payload = {
                "tool_id": tool_id,
                "args_schema": hydrated.args_schema,
                "examples": list(hydrated.examples),
                "constraints": hydrated.constraints,
            }
            return {
                "content": [{"type": "text", "text": json.dumps(payload)}],
                "isError": False,
            }
        if name == TOOL_EXECUTE:
            tool_id = args.get("tool_id")
            tool_args = args.get("args", {}) or {}
            if not isinstance(tool_id, str) or not isinstance(tool_args, dict):
                return envelope_call_result(
                    GatewayError(
                        code="ARGS_INVALID",
                        message="tool_execute requires string 'tool_id' and object 'args'.",
                    ),
                    label=TOOL_EXECUTE,
                )
            envelope = await runtime.execute(tool_id, tool_args)
            return envelope_call_result(envelope, label=TOOL_EXECUTE)
        return envelope_call_result(
            GatewayError(
                code="ARGS_INVALID",
                message=f"proxy mode does not expose meta-tool {name!r}",
            ),
            label=name,
        )
    return envelope_call_result(
        GatewayError(
            code="ARGS_INVALID",
            message=f"unsupported MCP method {method!r}",
        ),
        label=method,
    )


__all__ = [
    "ExposureMode",
    "PROXY_META_TOOL_NAMES",
    "TOOL_EXECUTE",
    "TOOL_HYDRATE",
    "dispatch_proxy_request",
    "make_proxy_meta_tools",
    "make_stripped_tools_list",
]

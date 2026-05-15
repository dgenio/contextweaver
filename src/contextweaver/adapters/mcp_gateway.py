"""Two-tool MCP gateway (#28) + ``tool_view`` meta-tool (#34).

The gateway exposes exactly **three** MCP meta-tools per
``docs/gateway_spec.md`` §4.2:

- ``tool_browse(query|path)`` — returns ``list[ChoiceCard]`` per §2.
- ``tool_execute(tool_id, args)`` — internal hydrate + jsonschema
  validation + upstream call + firewall.
- ``tool_view(handle, selector)`` — drilldown into a previously-stored
  artifact (#34).

This module provides two thin layers on top of
:class:`~contextweaver.adapters.proxy_runtime.ProxyRuntime`:

- :func:`make_gateway_meta_tools` — returns the three MCP tool
  definitions ready for an upstream ``tools/list``.
- :func:`dispatch_meta_tool` — invokes one of the three meta-tools and
  packages the response in the MCP wire format the agent's client
  expects.

The MCP server transport binding (stdio / SSE) lives in
:mod:`contextweaver.adapters.mcp_gateway_server`.  The two-layer split
keeps this module pure (no transport coupling) and synchronous for the
parts that do not need to await upstream.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, is_dataclass
from typing import Any

from contextweaver.adapters.gateway_error import GatewayError
from contextweaver.adapters.proxy_runtime import ProxyRuntime
from contextweaver.envelope import ChoiceCard, ResultEnvelope

logger = logging.getLogger("contextweaver.adapters.mcp_gateway")

TOOL_BROWSE = "tool_browse"
TOOL_EXECUTE = "tool_execute"
TOOL_VIEW = "tool_view"

GATEWAY_TOOL_NAMES = (TOOL_BROWSE, TOOL_EXECUTE, TOOL_VIEW)


def make_gateway_meta_tools(runtime: ProxyRuntime) -> list[dict[str, Any]]:
    """Return the three MCP meta-tool definitions for gateway mode (§4.2).

    The shape mirrors the MCP ``tools/list`` entry: ``name``,
    ``description``, ``inputSchema``.  No banned fields per §2.2.

    Args:
        runtime: A configured :class:`ProxyRuntime`.  Currently unused
            in shape (the meta-tools are statically defined) but accepted
            so future per-runtime customisation (e.g. exposing the
            configured top-k via the description) is non-breaking.

    Returns:
        A list of three MCP tool definition dicts.
    """
    _ = runtime  # reserved
    return [
        {
            "name": TOOL_BROWSE,
            "description": (
                "Browse the upstream tool catalog.  Pass exactly one of "
                "'query' (free-text) or 'path' (e.g. '/github/issues') and "
                "receive a bounded list of ChoiceCards."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": TOOL_EXECUTE,
            "description": (
                "Invoke an upstream tool by canonical tool_id.  Arguments "
                "are validated against the hydrated input schema before "
                "dispatch and the response is compacted via the context "
                "firewall."
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
        {
            "name": TOOL_VIEW,
            "description": (
                "Drill into a previously stored artifact handle and "
                "return a slice (head / lines / json_keys / rows) per the "
                "ArtifactStore.drilldown contract."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "handle": {"type": "string"},
                    "selector": {"type": "object"},
                },
                "required": ["handle", "selector"],
                "additionalProperties": False,
            },
        },
    ]


async def dispatch_meta_tool(
    runtime: ProxyRuntime,
    name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Invoke a gateway meta-tool by name and return an MCP-format response.

    The returned dict is the MCP ``CallToolResult`` shape (``content``,
    ``isError``).  Errors produced by :class:`ProxyRuntime` are
    serialised via :func:`envelope_call_result` so they are visible to
    the agent's client without raising across the MCP boundary.

    Args:
        runtime: The active :class:`ProxyRuntime`.
        name: One of :data:`GATEWAY_TOOL_NAMES`.
        args: Arguments coming off the MCP wire (already JSON-decoded).

    Returns:
        An MCP-format ``CallToolResult`` dict.
    """
    if name == TOOL_BROWSE:
        result = runtime.browse(
            query=args.get("query"),
            path=args.get("path"),
            top_k=args.get("top_k"),
        )
        return envelope_call_result(result, label="tool_browse")
    if name == TOOL_EXECUTE:
        tool_id = args.get("tool_id")
        tool_args = args.get("args", {}) or {}
        if not isinstance(tool_id, str) or not isinstance(tool_args, dict):
            return envelope_call_result(
                GatewayError(
                    code="ARGS_INVALID",
                    message="tool_execute requires string 'tool_id' and object 'args'.",
                ),
                label="tool_execute",
            )
        envelope = await runtime.execute(tool_id, tool_args)
        return envelope_call_result(envelope, label="tool_execute")
    if name == TOOL_VIEW:
        handle = args.get("handle")
        selector = args.get("selector", {}) or {}
        if not isinstance(handle, str) or not isinstance(selector, dict):
            return envelope_call_result(
                GatewayError(
                    code="ARGS_INVALID",
                    message="tool_view requires string 'handle' and object 'selector'.",
                ),
                label="tool_view",
            )
        sliced = runtime.view(handle, selector)
        return envelope_call_result(sliced, label="tool_view")
    return envelope_call_result(
        GatewayError(
            code="ARGS_INVALID",
            message=f"unknown meta-tool {name!r} (valid: {list(GATEWAY_TOOL_NAMES)})",
        ),
        label=name,
    )


def envelope_call_result(value: Any, *, label: str) -> dict[str, Any]:  # noqa: ANN401
    """Wrap *value* in an MCP ``CallToolResult`` dict.

    Shared by :mod:`mcp_gateway` and :mod:`mcp_proxy` — promoted to a
    public symbol so both siblings can import it without coupling
    through a private name.

    - :class:`GatewayError` → ``isError=True`` with the §3.4 JSON shape
      as a text content part.
    - :class:`ResultEnvelope` → ``isError`` mirrors the envelope status
      and the body carries the envelope JSON.
    - ``list[ChoiceCard]`` → ``isError=False`` with a JSON array body.
    - Any other value (e.g. a plain string from ``tool_view``) is
      serialised as JSON (or as plain text if not JSON-encodable).
    """
    _ = label
    if isinstance(value, GatewayError):
        return {
            "content": [{"type": "text", "text": json.dumps(value.to_dict())}],
            "isError": True,
        }
    if isinstance(value, ResultEnvelope):
        return {
            "content": [{"type": "text", "text": json.dumps(value.to_dict())}],
            "isError": value.status == "error",
        }
    if isinstance(value, list):
        payload = [c.to_dict() if isinstance(c, ChoiceCard) else c for c in value]
        return {
            "content": [{"type": "text", "text": json.dumps(payload)}],
            "isError": False,
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "content": [{"type": "text", "text": json.dumps(asdict(value))}],
            "isError": False,
        }
    if isinstance(value, str):
        return {
            "content": [{"type": "text", "text": value}],
            "isError": False,
        }
    try:
        return {
            "content": [{"type": "text", "text": json.dumps(value)}],
            "isError": False,
        }
    except TypeError:
        return {
            "content": [{"type": "text", "text": repr(value)}],
            "isError": False,
        }

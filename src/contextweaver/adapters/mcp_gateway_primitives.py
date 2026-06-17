"""Gateway meta-tools for MCP resources and prompts (#669 / #670).

Sibling to :mod:`contextweaver.adapters.mcp_gateway` (which defines the three
tool meta-tools).  This module defines the four primitive meta-tools that
surface MCP resources and prompts through the *same* bounded-choice gateway
surface — distinct verbs that match MCP's distinct semantics rather than
overloading ``tool_execute`` (``docs/gateway_spec.md`` §9):

- ``resource_browse(query|path)`` — bounded ``list[ChoiceCard]`` of resources.
- ``resource_read(resource_id)`` — read a resource, firewalled.
- ``prompt_browse(query|path)`` — bounded ``list[ChoiceCard]`` of prompts.
- ``prompt_get(prompt_id, args)`` — fetch a rendered prompt, firewalled.

Like :mod:`mcp_gateway`, this layer is pure: it builds the MCP tool
definitions and dispatches to a :class:`PrimitiveGatewayRuntime`, packaging
responses with the shared :func:`~contextweaver.adapters.mcp_gateway.envelope_call_result`.
The transport binding lives in
:mod:`contextweaver.adapters.mcp_gateway_server`.
"""

from __future__ import annotations

import logging
from typing import Any

from contextweaver.adapters.gateway_error import GatewayError
from contextweaver.adapters.gateway_primitives import PrimitiveGatewayRuntime
from contextweaver.adapters.mcp_gateway import envelope_call_result

logger = logging.getLogger("contextweaver.adapters.mcp_gateway_primitives")

RESOURCE_BROWSE = "resource_browse"
RESOURCE_READ = "resource_read"
PROMPT_BROWSE = "prompt_browse"
PROMPT_GET = "prompt_get"

PRIMITIVE_TOOL_NAMES = (RESOURCE_BROWSE, RESOURCE_READ, PROMPT_BROWSE, PROMPT_GET)

_BROWSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "path": {"type": "string"},
        "top_k": {"type": "integer", "minimum": 1, "maximum": 50},
    },
    "additionalProperties": False,
}


def make_primitive_meta_tools() -> list[dict[str, Any]]:
    """Return the four resource/prompt meta-tool definitions (§9).

    The definitions are static; dispatch is bound to a runtime separately via
    :func:`dispatch_primitive_meta_tool`.

    Returns:
        A list of four MCP tool definition dicts with no banned fields (§2.2).
    """
    return [
        {
            "name": RESOURCE_BROWSE,
            "description": (
                "Browse the upstream MCP resource catalog. Pass exactly one of "
                "'query' (free-text) or 'path' and receive a bounded list of "
                "ChoiceCards (kind='resource'). Read one with resource_read."
            ),
            "inputSchema": _BROWSE_SCHEMA,
        },
        {
            "name": RESOURCE_READ,
            "description": (
                "Read an upstream resource by canonical resource_id (from "
                "resource_browse). The content is compacted via the context "
                "firewall; large reads are addressable via tool_view."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"resource_id": {"type": "string"}},
                "required": ["resource_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": PROMPT_BROWSE,
            "description": (
                "Browse the upstream MCP prompt catalog. Pass exactly one of "
                "'query' (free-text) or 'path' and receive a bounded list of "
                "ChoiceCards (kind='prompt'). Fetch one with prompt_get."
            ),
            "inputSchema": _BROWSE_SCHEMA,
        },
        {
            "name": PROMPT_GET,
            "description": (
                "Fetch a rendered prompt by canonical prompt_id (from "
                "prompt_browse). Arguments are validated against the prompt's "
                "declared argument schema before dispatch."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"prompt_id": {"type": "string"}, "args": {"type": "object"}},
                "required": ["prompt_id"],
                "additionalProperties": False,
            },
        },
    ]


async def dispatch_primitive_meta_tool(
    runtime: PrimitiveGatewayRuntime,
    name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Invoke a primitive meta-tool by name and return an MCP-format response.

    Args:
        runtime: The active :class:`PrimitiveGatewayRuntime`.
        name: One of :data:`PRIMITIVE_TOOL_NAMES`.
        args: Arguments coming off the MCP wire (already JSON-decoded).

    Returns:
        An MCP-format ``CallToolResult`` dict.  Runtime errors are serialised
        via :func:`~contextweaver.adapters.mcp_gateway.envelope_call_result`
        without raising across the MCP boundary.
    """
    if name == RESOURCE_BROWSE:
        result = runtime.browse_resources(
            query=args.get("query"), path=args.get("path"), top_k=args.get("top_k")
        )
        return envelope_call_result(result, label=RESOURCE_BROWSE)
    if name == PROMPT_BROWSE:
        result = runtime.browse_prompts(
            query=args.get("query"), path=args.get("path"), top_k=args.get("top_k")
        )
        return envelope_call_result(result, label=PROMPT_BROWSE)
    if name == RESOURCE_READ:
        resource_id = args.get("resource_id")
        if not isinstance(resource_id, str):
            return envelope_call_result(
                GatewayError(
                    code="ARGS_INVALID", message="resource_read requires string 'resource_id'."
                ),
                label=RESOURCE_READ,
            )
        return envelope_call_result(await runtime.read_resource(resource_id), label=RESOURCE_READ)
    if name == PROMPT_GET:
        prompt_id = args.get("prompt_id")
        prompt_args = args.get("args", {}) or {}
        if not isinstance(prompt_id, str) or not isinstance(prompt_args, dict):
            return envelope_call_result(
                GatewayError(
                    code="ARGS_INVALID",
                    message="prompt_get requires string 'prompt_id' and object 'args'.",
                ),
                label=PROMPT_GET,
            )
        return envelope_call_result(
            await runtime.get_prompt(prompt_id, prompt_args), label=PROMPT_GET
        )
    return envelope_call_result(
        GatewayError(
            code="ARGS_INVALID",
            message=f"unknown meta-tool {name!r} (valid: {list(PRIMITIVE_TOOL_NAMES)})",
        ),
        label=name,
    )

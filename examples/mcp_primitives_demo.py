"""Gateway resource & prompt meta-tools end-to-end demo (#669 / #670).

Companion to ``mcp_gateway_demo.py`` (which covers the three *tool* meta-tools).
This demo shapes the other two MCP primitives — resources and prompts — through
the same bounded-choice gateway surface (#555), exposing four meta-tools per
``docs/gateway_spec.md`` §9:

- ``resource_browse(query|path)`` / ``resource_read(resource_id)``
- ``prompt_browse(query|path)`` / ``prompt_get(prompt_id, args)``

It wires an in-process ``PrimitiveUpstream`` stub so it runs without network
access or an MCP-SDK transport — exactly what ``make example`` exercises in CI.
Swap the stub for a real upstream client to front a live MCP server.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast

from contextweaver.adapters.gateway_primitives import PrimitiveGatewayRuntime
from contextweaver.adapters.mcp_gateway_primitives import (
    PROMPT_BROWSE,
    PROMPT_GET,
    RESOURCE_BROWSE,
    RESOURCE_READ,
    dispatch_primitive_meta_tool,
    make_primitive_meta_tools,
)

RESOURCES = [
    {
        "uri": "file:///docs/readme.md",
        "name": "README",
        "description": "Project overview.",
        "mimeType": "text/markdown",
    },
    {
        "uri": "file:///docs/architecture.md",
        "name": "Architecture",
        "description": "System design notes.",
        "mimeType": "text/markdown",
    },
    {
        "uri": "postgres://prod/users",
        "name": "users table",
        "description": "Production user records.",
    },
]
PROMPTS = [
    {
        "name": "summarize_pr",
        "description": "Summarize a pull request for reviewers.",
        "arguments": [{"name": "repo", "required": True}, {"name": "number", "required": True}],
    },
    {
        "name": "translate",
        "description": "Translate text to another language.",
        "arguments": [{"name": "text", "required": True}],
    },
]


class DemoPrimitiveUpstream:
    """In-process :class:`PrimitiveUpstream` stub — no network, no MCP SDK."""

    async def list_resources(self) -> list[dict[str, Any]]:
        return [dict(r) for r in RESOURCES]

    async def read_resource(self, uri: str) -> dict[str, Any]:
        return {
            "contents": [{"uri": uri, "mimeType": "text/plain", "text": f"<contents of {uri}>"}]
        }

    async def list_prompts(self) -> list[dict[str, Any]]:
        return [dict(p) for p in PROMPTS]

    async def get_prompt(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "description": f"Rendered {name}",
            "messages": [
                {"role": "user", "content": {"type": "text", "text": f"{name} {arguments}"}}
            ],
        }


def _cards(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return cast("list[dict[str, Any]]", json.loads(payload["content"][0]["text"]))


async def main() -> None:
    runtime = PrimitiveGatewayRuntime(DemoPrimitiveUpstream())
    n_res, n_prompt = await runtime.refresh()
    print(f"Registered {n_res} resources and {n_prompt} prompts behind the gateway.\n")

    print("Meta-tools exposed to the client:")
    for tool in make_primitive_meta_tools(runtime):
        print(f"  - {tool['name']}")
    print()

    # 1. Browse resources, then read the top hit.
    browse = await dispatch_primitive_meta_tool(runtime, RESOURCE_BROWSE, {"query": "design notes"})
    resource_cards = _cards(browse)
    print(f"resource_browse('design notes') -> {len(resource_cards)} ChoiceCard(s):")
    for card in resource_cards:
        print(f"  [{card['kind']}] {card['id']}  ({card['name']})")
    top_resource = resource_cards[0]["id"]
    read = await dispatch_primitive_meta_tool(runtime, RESOURCE_READ, {"resource_id": top_resource})
    print(f"\nresource_read({top_resource}) -> isError={read['isError']}")
    print(f"  {read['content'][0]['text'][:120]}\n")

    # 2. Browse prompts, then fetch one with validated arguments.
    browse = await dispatch_primitive_meta_tool(
        runtime, PROMPT_BROWSE, {"query": "summarize a pull request"}
    )
    prompt_cards = _cards(browse)
    print(f"prompt_browse('summarize a pull request') -> {len(prompt_cards)} ChoiceCard(s):")
    for card in prompt_cards:
        print(f"  [{card['kind']}] {card['id']}  ({card['name']})")
    top_prompt = next(c["id"] for c in prompt_cards if "summarize" in c["id"])

    bad = await dispatch_primitive_meta_tool(
        runtime, PROMPT_GET, {"prompt_id": top_prompt, "args": {"repo": "acme/app"}}
    )
    print(
        f"\nprompt_get(missing 'number') -> isError={bad['isError']} "
        f"({json.loads(bad['content'][0]['text'])['error']})"
    )
    good = await dispatch_primitive_meta_tool(
        runtime, PROMPT_GET, {"prompt_id": top_prompt, "args": {"repo": "acme/app", "number": "42"}}
    )
    print(f"prompt_get(valid args) -> isError={good['isError']}")


if __name__ == "__main__":
    asyncio.run(main())

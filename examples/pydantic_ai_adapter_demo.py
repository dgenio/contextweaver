"""Pydantic AI adapter demo (issue #272).

Demonstrates the two contextweaver entry points for a Pydantic AI agent:

1. Routing — convert Pydantic AI tool definitions into a contextweaver
   :class:`~contextweaver.routing.catalog.Catalog`, build a routing graph,
   and score a query against it to get a bounded shortlist.

2. Message ingestion — convert a Pydantic AI ``ModelMessage`` transcript
   into :class:`~contextweaver.types.ContextItem`s, then round-trip back to
   the original message dicts (proves the encoder is the inverse of the
   decoder).

Uses plain dicts matching ``pydantic_ai.tools.Tool.model_dump()`` /
``ModelMessage.model_dump()`` — no ``pydantic_ai`` install required for this
demo.  For live conversion of real :class:`pydantic_ai.Tool` instances,
install ``contextweaver[pydantic-ai]`` and call
:func:`contextweaver.adapters.pydantic_ai.load_pydantic_ai_catalog`.
"""

from __future__ import annotations

from contextweaver.adapters.pydantic_ai import (
    from_pydantic_ai_messages,
    infer_pydantic_ai_namespace,
    pydantic_ai_tool_to_selectable,
    pydantic_ai_tools_to_catalog,
    to_pydantic_ai_messages,
)
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder

# Simulated Pydantic AI tool definitions (the shape ``Tool.model_dump()``
# emits — see https://ai.pydantic.dev/tools/).
PYDANTIC_AI_TOOLS: list[dict[str, object]] = [
    {
        "name": "github_search_repos",
        "description": "Search GitHub repositories by keyword.",
        "parameters_json_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
        "tags": ["vcs", "search"],
        "takes_ctx": False,
    },
    {
        "name": "github_open_issue",
        "description": "Open a new issue in a GitHub repository.",
        "parameters_json_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["repo", "title"],
        },
        "tags": ["vcs"],
        "takes_ctx": True,
    },
    {
        "name": "calendar.create_event",
        "description": "Schedule a calendar event.",
        "tags": ["scheduling"],
    },
    {
        "name": "weather_get_forecast",
        "description": "Fetch the weather forecast for a city.",
        "parameters_json_schema": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
]

# A representative Pydantic AI message transcript.  Pydantic AI groups
# user prompts and tool returns under ``kind="request"`` and groups
# assistant text and tool calls under ``kind="response"``.
PYDANTIC_AI_TRANSCRIPT: list[dict[str, object]] = [
    {
        "kind": "request",
        "parts": [
            {"part_kind": "system-prompt", "content": "You are a helpful assistant."},
            {"part_kind": "user-prompt", "content": "What's the weather in Lisbon?"},
        ],
    },
    {
        "kind": "response",
        "parts": [
            {
                "part_kind": "tool-call",
                "tool_name": "weather_get_forecast",
                "tool_call_id": "call-001",
                "args": {"city": "Lisbon"},
            },
        ],
    },
    {
        "kind": "request",
        "parts": [
            {
                "part_kind": "tool-return",
                "tool_name": "weather_get_forecast",
                "tool_call_id": "call-001",
                "content": "23 C, partly cloudy",
            },
        ],
    },
    {
        "kind": "response",
        "parts": [
            {
                "part_kind": "text",
                "content": "It's 23 C and partly cloudy in Lisbon.",
            },
        ],
    },
]


def main() -> None:
    print("=== Pydantic AI Adapter Demo ===\n")

    # 1. Namespace inference.
    print("[1] Namespace inference:")
    for name in (
        "github_search_repos",
        "calendar.create_event",
        "weather_get_forecast",
    ):
        print(f"    {name!r:30s} -> namespace={infer_pydantic_ai_namespace(name)!r}")

    # 2. Single conversion.
    print("\n[2] Single tool conversion:")
    item = pydantic_ai_tool_to_selectable(PYDANTIC_AI_TOOLS[0])
    print(f"    ID:        {item.id}")
    print(f"    Name:      {item.name}")
    print(f"    Namespace: {item.namespace}")
    print(f"    Tags:      {item.tags}")
    print(f"    Schema:    {sorted(item.args_schema.get('properties', {}).keys())}")

    # 3. Batch conversion → Catalog.
    print("\n[3] Building Catalog from 4 Pydantic AI tools:")
    catalog = pydantic_ai_tools_to_catalog(PYDANTIC_AI_TOOLS)
    for it in catalog.all():
        print(f"    {it.id:40s} ns={it.namespace:12s} tags={sorted(it.tags)}")

    # 4. Routing — narrow to a top-3 shortlist for a real query.
    print("\n[4] Routing the query 'open an issue in a typescript repo':")
    graph = TreeBuilder(max_children=8).build(catalog.all())
    router = Router(graph, items=catalog.all(), top_k=3)
    result = router.route("open an issue in a typescript repo")
    for rank, (sel, score) in enumerate(
        zip(result.candidate_items, result.scores, strict=False), 1
    ):
        print(f"    #{rank} {sel.id:40s} score={score:.3f}")

    # 5. Message round-trip — decode → encode should rebuild the input.
    print("\n[5] Message round-trip:")
    items = from_pydantic_ai_messages(PYDANTIC_AI_TRANSCRIPT)
    print(f"    decoded {len(items)} ContextItem(s):")
    for ci in items:
        suffix = f" parent={ci.parent_id}" if ci.parent_id else ""
        print(f"      {ci.kind.value:14s} id={ci.id}{suffix}")
    rebuilt = to_pydantic_ai_messages(items)
    print(f"    re-encoded {len(rebuilt)} message(s) — round-trip lossless: ", end="")
    print(rebuilt == PYDANTIC_AI_TRANSCRIPT)

    print("\nDone.")


if __name__ == "__main__":
    main()

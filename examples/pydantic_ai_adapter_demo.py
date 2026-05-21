"""Pydantic AI adapter demo (issue #272).

Demonstrates converting Pydantic AI tool definitions into contextweaver
``SelectableItem``\\s, routing a query against the resulting catalog,
and round-tripping a Pydantic AI ``ModelMessage`` history through
``ContextItem``\\s.  Uses plain dicts matching the
``pydantic_ai.tools.Tool.model_dump()`` / ``ModelMessage.model_dump()``
shapes — no ``pydantic_ai`` install required for this demo.

For live conversion of real :class:`pydantic_ai.tools.Tool` instances,
install the optional extra: ``pip install 'contextweaver[pydantic-ai]'``
and call :func:`contextweaver.adapters.pydantic_ai.load_pydantic_ai_catalog`.
"""

from __future__ import annotations

from contextweaver.adapters.pydantic_ai import (
    infer_pydantic_ai_namespace,
    pydantic_ai_tool_to_selectable,
    pydantic_ai_tools_to_catalog,
)
from contextweaver.adapters.pydantic_ai_messages import (
    from_pydantic_ai_messages,
    to_pydantic_ai_messages,
)
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder

# Simulated tools as they would appear from a Pydantic AI agent's
# ``FunctionToolset`` (https://ai.pydantic.dev/tools/).  Names use the
# underscore-prefixed namespace convention.
PYDANTIC_AI_TOOLS: list[dict[str, object]] = [
    {
        "name": "github_search_repos",
        "description": "Search GitHub repositories by keyword.",
        "parameters_json_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["query"],
        },
        "tags": ["vcs", "search"],
        "strict": True,
    },
    {
        "name": "github_create_issue",
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
        "strict": True,
    },
    {
        "name": "slack_send_message",
        "description": "Send a Slack message to a channel.",
        "parameters_json_schema": {
            "type": "object",
            "properties": {"channel": {"type": "string"}, "text": {"type": "string"}},
            "required": ["channel", "text"],
        },
        "tags": ["messaging"],
    },
    {
        "name": "calendar.create_event",
        "description": "Schedule a calendar event for a participant set.",
        "tags": ["scheduling", "write"],
    },
]

# Simulated Pydantic AI message history (the ``.model_dump()`` shape).
PYDANTIC_AI_MESSAGES: list[dict[str, object]] = [
    {
        "kind": "request",
        "parts": [
            {"part_kind": "system-prompt", "content": "You are a helpful agent."},
            {"part_kind": "user-prompt", "content": "Find typescript repos."},
        ],
    },
    {
        "kind": "response",
        "parts": [
            {"part_kind": "text", "content": "I'll search GitHub first."},
            {
                "part_kind": "tool-call",
                "tool_call_id": "call_001",
                "tool_name": "github_search_repos",
                "args": {"query": "typescript", "limit": 5},
            },
        ],
    },
    {
        "kind": "request",
        "parts": [
            {
                "part_kind": "tool-return",
                "tool_call_id": "call_001",
                "tool_name": "github_search_repos",
                "content": "5 results returned",
            },
        ],
    },
]


def main() -> None:
    print("=== Pydantic AI Adapter Demo ===\n")

    print("[1] Namespace inference:")
    for name in (
        "github_search_repos",
        "slack_send_message",
        "calendar.create_event",
    ):
        print(f"    {name!r:30s} → namespace={infer_pydantic_ai_namespace(name)!r}")

    print("\n[2] Single tool conversion:")
    item = pydantic_ai_tool_to_selectable(PYDANTIC_AI_TOOLS[0])  # type: ignore[arg-type]
    print(f"    ID:        {item.id}")
    print(f"    Name:      {item.name}")
    print(f"    Namespace: {item.namespace}")
    print(f"    Tags:      {item.tags}")
    print(f"    Strict:    {item.metadata.get('strict')}")

    print("\n[3] Building Catalog from 4 Pydantic AI tools:")
    catalog = pydantic_ai_tools_to_catalog(PYDANTIC_AI_TOOLS)  # type: ignore[arg-type]
    for it in catalog.all():
        print(f"    {it.id:40s} ns={it.namespace:12s} tags={sorted(it.tags)}")

    print("\n[4] Routing the query 'find typescript repos and open an issue':")
    graph = TreeBuilder(max_children=8).build(catalog.all())
    router = Router(graph, items=catalog.all(), top_k=3)
    result = router.route("find typescript repos and open an issue")
    for rank, (item, score) in enumerate(
        zip(result.candidate_items, result.scores, strict=False), 1
    ):
        print(f"    #{rank} {item.id:40s} score={score:.3f}")

    print("\n[5] Round-tripping a 3-message history through ContextItems:")
    items = from_pydantic_ai_messages(PYDANTIC_AI_MESSAGES)
    print(f"    decoded items: {len(items)} (one ContextItem per part)")
    for ci in items:
        print(
            f"    - {ci.id:40s} kind={ci.kind.value:12s} part_kind={ci.metadata.get('part_kind')!r}"
        )
    round_tripped = to_pydantic_ai_messages(items)
    print(f"    re-encoded messages: {len(round_tripped)}")
    # ``args`` are normalised to a canonical JSON string on encode
    # (Pydantic AI's wire shape uses a string, not a dict).
    tool_call = round_tripped[1]["parts"][1]  # type: ignore[index]
    assert tool_call["args"] == '{"limit": 5, "query": "typescript"}'
    print("    round-trip canonical args ✓")

    print("\nDone.")


if __name__ == "__main__":
    main()

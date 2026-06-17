"""Microsoft Agent Framework adapter demo (issue #430).

Demonstrates the two contextweaver entry points for a Microsoft Agent
Framework (AutoGen / Semantic Kernel lineage) agent:

1. Routing — convert Agent Framework ``AIFunction`` tools (in their plain-dict
   form) into a contextweaver :class:`~contextweaver.routing.catalog.Catalog`,
   build a routing graph, and score a query against it for a bounded shortlist.

2. Thread ingestion — convert a thread's ``ChatMessage`` history (user /
   assistant text, function calls, and function results) into
   :class:`~contextweaver.types.ContextItem`s so prior turns and tool calls
   flow through the contextweaver pipeline with correct parent chains.

Uses plain dicts matching the Agent Framework wire shapes, so no
``agent-framework`` install is required for this demo.  For live conversion of
real objects, install ``contextweaver[agent-framework]`` and call
:func:`contextweaver.adapters.agent_framework.load_agent_framework_catalog`.
"""

from __future__ import annotations

from contextweaver.adapters.agent_framework import (
    agent_framework_tools_to_catalog,
    from_agent_framework_thread,
)
from contextweaver.routing.cards import cards_for_route, format_card_for_prompt
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder

TOOLS: list[dict[str, object]] = [
    {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
    {
        "name": "send_email",
        "description": "Send an email to a recipient.",
        "parameters": {
            "type": "object",
            "properties": {"to": {"type": "string"}, "body": {"type": "string"}},
            "required": ["to"],
        },
    },
    {
        "name": "create_ticket",
        "description": "Open a support ticket in the tracker.",
        "parameters": {"type": "object", "properties": {"title": {"type": "string"}}},
    },
]

THREAD: list[dict[str, object]] = [
    {"role": "user", "contents": [{"text": "What's the weather in Paris?"}]},
    {
        "role": "assistant",
        "contents": [{"name": "get_weather", "call_id": "c1", "arguments": {"city": "Paris"}}],
    },
    {"role": "tool", "contents": [{"call_id": "c1", "result": {"tempC": 19, "sky": "clear"}}]},
]


def main() -> None:
    catalog = agent_framework_tools_to_catalog(TOOLS)
    items = catalog.all()
    router = Router(TreeBuilder().build(items), items=items, beam_width=2)

    query = "let the user know what the weather looks like"
    print(f"Loaded {len(items)} Agent Framework tools.")
    print(f"Query: {query!r}\n")
    result = router.route(query)
    print("Bounded tool shortlist:")
    for card in cards_for_route(result.candidate_ids, catalog):
        print(format_card_for_prompt(card))

    items_ingested = from_agent_framework_thread(THREAD)
    print(f"\nIngested {len(items_ingested)} context items from the thread:")
    for item in items_ingested:
        parent = f" (parent={item.parent_id})" if item.parent_id else ""
        print(f"  {item.kind.value}: {item.text}{parent}")


if __name__ == "__main__":
    main()

"""Agno adapter demo (issue #275).

Demonstrates the two contextweaver entry points for an Agno (formerly
Phidata) agent:

1. Routing — convert Agno ``Function`` / ``Toolkit`` member definitions
   (in their plain-dict form) into a contextweaver
   :class:`~contextweaver.routing.catalog.Catalog`, build a routing graph,
   and score a query against it to get a bounded shortlist.

2. Session ingestion — convert an Agno ``AgentSession``'s message history
   into :class:`~contextweaver.types.ContextItem`s so prior turns and tool
   calls flow through the contextweaver pipeline.

Positioning note (per issue #275): contextweaver replaces only the
prompt-assembly step.  Agno's ``Memory`` / ``Storage`` / ``Knowledge``
layer remains authoritative for long-lived state — see
``docs/integration_agno.md`` for the layering diagram.

Uses plain dicts matching ``agno.tools.Function.to_dict()`` and the
OpenAI-style message shape ``AgentSession`` emits.  No ``agno`` install
required for this demo.  For live conversion of real Agno objects, install
``contextweaver[agno]`` and call
:func:`contextweaver.adapters.agno.load_agno_catalog`.
"""

from __future__ import annotations

from contextweaver.adapters.agno import (
    agno_tool_to_selectable,
    agno_tools_to_catalog,
    from_agno_session,
    infer_agno_namespace,
)
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder

# Simulated Agno tool definitions — the dict shape mirrors what
# ``agno.tools.Function.to_dict()`` emits: OpenAI-style function-call
# JSON Schema in ``parameters`` plus an optional ``toolkit_name``.
AGNO_TOOLS: list[dict[str, object]] = [
    {
        "name": "duckduckgo_search",
        "description": "Search DuckDuckGo for a query.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        "toolkit_name": "duckduckgo",
        "tags": ["search"],
    },
    {
        "name": "duckduckgo_news",
        "description": "Fetch the latest DuckDuckGo news for a query.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["query"],
        },
        "toolkit_name": "duckduckgo",
        "tags": ["search", "news"],
    },
    {
        "name": "yfinance_get_company_info",
        "description": "Return basic company information from Yahoo Finance.",
        "parameters": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
        "toolkit_name": "yfinance",
        "tags": ["finance"],
    },
    {
        "name": "calculator_evaluate",
        "description": "Evaluate a mathematical expression.",
        "parameters": {
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
        "tags": ["math"],
    },
]

# A representative Agno session message history.  Agno uses the OpenAI
# Chat Completions message shape (``role`` / ``content`` / ``tool_calls`` /
# ``tool_call_id``).
AGNO_SESSION_MESSAGES: list[dict[str, object]] = [
    {"role": "system", "content": "You are a financial-news assistant."},
    {"role": "user", "content": "What's NVDA's company info?"},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call-yfin-1",
                "type": "function",
                "function": {
                    "name": "yfinance_get_company_info",
                    "arguments": '{"ticker": "NVDA"}',
                },
            },
        ],
    },
    {
        "role": "tool",
        "tool_call_id": "call-yfin-1",
        "name": "yfinance_get_company_info",
        "content": "NVIDIA Corp · semiconductors · Santa Clara · CEO Jensen Huang",
    },
    {
        "role": "assistant",
        "content": (
            "NVIDIA Corp is a semiconductors company based in Santa Clara, led by Jensen Huang."
        ),
    },
]


def main() -> None:
    print("=== Agno Adapter Demo ===\n")

    # 1. Namespace inference.
    print("[1] Namespace inference:")
    for name in ("duckduckgo_search", "yfinance_get_company_info", "calculator_evaluate"):
        print(f"    {name!r:35s} → namespace={infer_agno_namespace(name)!r}")

    # 2. Single conversion.
    print("\n[2] Single tool conversion:")
    item = agno_tool_to_selectable(AGNO_TOOLS[0])  # type: ignore[arg-type]
    print(f"    ID:         {item.id}")
    print(f"    Name:       {item.name}")
    print(f"    Namespace:  {item.namespace}")
    print(f"    Toolkit:    {item.metadata.get('toolkit_name')}")
    print(f"    Tags:       {item.tags}")

    # 3. Batch conversion → Catalog.
    print("\n[3] Building Catalog from 4 Agno tools:")
    catalog = agno_tools_to_catalog(AGNO_TOOLS)  # type: ignore[arg-type]
    for it in catalog.all():
        print(f"    {it.id:40s} ns={it.namespace:12s} tags={sorted(it.tags)}")

    # 4. Routing — narrow to a top-3 shortlist.
    print("\n[4] Routing the query 'company information about a stock ticker':")
    graph = TreeBuilder(max_children=8).build(catalog.all())
    router = Router(graph, items=catalog.all(), top_k=3)
    result = router.route("company information about a stock ticker")
    for rank, (sel, score) in enumerate(
        zip(result.candidate_items, result.scores, strict=False), 1
    ):
        print(f"    #{rank} {sel.id:40s} score={score:.3f}")

    # 5. Session-history ingestion.
    print("\n[5] Session-history ingestion:")
    items = from_agno_session(AGNO_SESSION_MESSAGES)  # type: ignore[arg-type]
    print(f"    {len(items)} ContextItem(s) produced:")
    for ci in items:
        suffix = f" parent={ci.parent_id}" if ci.parent_id else ""
        text_preview = ci.text[:50] + "..." if len(ci.text) > 50 else ci.text
        print(f"      {ci.kind.value:13s} id={ci.id:34s} {text_preview!r}{suffix}")

    print("\nDone.")


if __name__ == "__main__":
    main()

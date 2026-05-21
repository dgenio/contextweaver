"""Agno adapter demo (issue #275).

Demonstrates converting Agno tool definitions into contextweaver
``SelectableItem``\\s, routing a query against the resulting catalog,
and ingesting a simulated ``Agent.memory.messages`` history.  Uses
plain dicts matching the ``agno.tools.function.Function`` attribute
shape — no ``agno`` install required for this demo.

For live conversion of real Agno ``Function`` / ``Toolkit`` instances,
install the optional extra: ``pip install 'contextweaver[agno]'`` and
call :func:`contextweaver.adapters.agno.load_agno_catalog`.
"""

from __future__ import annotations

from contextweaver.adapters.agno import (
    agno_tool_to_selectable,
    agno_tools_to_catalog,
    infer_agno_namespace,
)
from contextweaver.adapters.agno_messages import from_agno_agent
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder

# Simulated tools as they would appear from an Agno ``Toolkit``'s
# ``functions`` dict (https://docs.agno.com/concepts/tools/).
AGNO_TOOLS: list[dict[str, object]] = [
    {
        "name": "duckduckgo_search",
        "description": "Run a DuckDuckGo web search and return ranked snippets.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer"},
            },
            "required": ["query"],
        },
        "tags": ["search"],
        "toolkit": "DuckDuckGoTools",
    },
    {
        "name": "wikipedia_search",
        "description": "Search and read Wikipedia article summaries.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        "tags": ["search", "reference"],
        "toolkit": "WikipediaTools",
    },
    {
        "name": "yfinance_get_company_info",
        "description": "Get company information from Yahoo Finance for a ticker.",
        "parameters": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
        "tags": ["finance"],
        "toolkit": "YFinanceTools",
        "show_result": True,
    },
    {
        "name": "calculator_evaluate",
        "description": "Evaluate a simple arithmetic expression.",
        "parameters": {
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
        "tags": ["math"],
        "toolkit": "CalculatorTools",
    },
]

# Simulated Agno ``Agent.memory.messages`` after a single 4-message run.
AGNO_MESSAGES: list[dict[str, object]] = [
    {"role": "system", "content": "You are a financial-research agent."},
    {"role": "user", "content": "Look up NVIDIA's company info."},
    {
        "role": "assistant",
        "content": "I'll fetch the latest info from Yahoo Finance.",
        "tool_calls": [
            {
                "id": "call_001",
                "type": "function",
                "function": {
                    "name": "yfinance_get_company_info",
                    "arguments": '{"ticker": "NVDA"}',
                },
            }
        ],
    },
    {
        "role": "tool",
        "tool_call_id": "call_001",
        "name": "yfinance_get_company_info",
        "content": "NVIDIA Corporation designs GPUs and computing platforms...",
    },
]


def main() -> None:
    print("=== Agno Adapter Demo ===\n")

    print("[1] Namespace inference:")
    for name in (
        "duckduckgo_search",
        "yfinance_get_company_info",
        "calculator_evaluate",
    ):
        print(f"    {name!r:30s} → namespace={infer_agno_namespace(name)!r}")

    print("\n[2] Single tool conversion:")
    item = agno_tool_to_selectable(AGNO_TOOLS[2])  # type: ignore[arg-type]
    print(f"    ID:        {item.id}")
    print(f"    Name:      {item.name}")
    print(f"    Namespace: {item.namespace}")
    print(f"    Tags:      {item.tags}")
    print(f"    Toolkit:   {item.metadata.get('toolkit')!r}")
    print(f"    Required:  {item.args_schema.get('required')}")

    print("\n[3] Building Catalog from 4 Agno tools:")
    catalog = agno_tools_to_catalog(AGNO_TOOLS)  # type: ignore[arg-type]
    for it in catalog.all():
        print(f"    {it.id:42s} ns={it.namespace:12s} toolkit={it.metadata.get('toolkit')!r}")

    print("\n[4] Routing the query 'find NVIDIA company info':")
    graph = TreeBuilder(max_children=8).build(catalog.all())
    router = Router(graph, items=catalog.all(), top_k=3)
    result = router.route("find NVIDIA company info")
    for rank, (it, score) in enumerate(zip(result.candidate_items, result.scores, strict=False), 1):
        print(f"    #{rank} {it.id:42s} score={score:.3f}")

    print("\n[5] Ingesting a 4-message Agno run into ContextItems:")
    items = from_agno_agent(AGNO_MESSAGES)
    print(f"    decoded items: {len(items)}")
    for ci in items:
        print(
            f"    - {ci.id:32s} kind={ci.kind.value:12s} "
            f"role={ci.metadata.get('role')!r} "
            f"tool_call_id={ci.metadata.get('tool_call_id')!r}"
        )

    print("\nDone.")


if __name__ == "__main__":
    main()

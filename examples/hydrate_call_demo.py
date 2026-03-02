"""Hydrate and call-prompt demo.

Demonstrates the Route → Hydrate → Call prompt pipeline:
1. Build a catalog with tools that have schemas, constraints, and examples.
2. Route a user query to find the best tool.
3. Hydrate the selected tool to retrieve full schema details.
4. Build a call-phase prompt with the schema injected.
"""

from __future__ import annotations

from contextweaver.context.manager import ContextManager
from contextweaver.routing.catalog import Catalog
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.types import ContextItem, ItemKind, SelectableItem


def _build_catalog() -> Catalog:
    """Create a small catalog with schema-rich tools."""
    catalog = Catalog()
    tools = [
        SelectableItem(
            id="db_query",
            kind="tool",
            name="query_database",
            description="Execute a SQL query against the analytics database",
            tags=["data", "sql"],
            namespace="db",
            args_schema={
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "SQL statement to execute"},
                    "timeout_ms": {"type": "integer", "default": 5000},
                },
                "required": ["sql"],
            },
            constraints={"max_rows": 1000, "read_only": True},
            examples=[
                'query_database(sql="SELECT COUNT(*) FROM users")',
                'query_database(sql="SELECT name FROM orders LIMIT 10", timeout_ms=3000)',
            ],
        ),
        SelectableItem(
            id="send_email",
            kind="tool",
            name="send_email",
            description="Send an email notification to a recipient",
            tags=["comm", "email"],
            namespace="comms",
            side_effects=True,
            args_schema={
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["to", "subject", "body"],
            },
        ),
        SelectableItem(
            id="search_web",
            kind="tool",
            name="web_search",
            description="Search the web for information",
            tags=["search", "web"],
            namespace="web",
        ),
    ]
    for tool in tools:
        catalog.register(tool)
    return catalog


def main() -> None:
    catalog = _build_catalog()

    # --- Step 1: Route the query to find the best tool ---
    items = catalog.all()
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items, beam_width=3)

    query = "How many users signed up this month?"
    result = router.route(query)
    top_tool_id = result.candidate_ids[0]
    print(f"Query: {query!r}")
    print(f"Routed to: {top_tool_id}")

    # --- Step 2: Hydrate the selected tool ---
    hydration = catalog.hydrate(top_tool_id)
    print(f"\nHydrated tool: {hydration.item.name}")
    print(f"  Schema keys: {list(hydration.args_schema.get('properties', {}).keys())}")
    print(f"  Constraints: {hydration.constraints}")
    print(f"  Examples: {len(hydration.examples)}")

    # --- Step 3: Build the call-phase prompt ---
    log = InMemoryEventLog()
    log.append(ContextItem(id="u1", kind=ItemKind.user_turn, text=query))
    mgr = ContextManager(event_log=log)

    pack = mgr.build_call_prompt_sync(
        tool_id=top_tool_id,
        query=query,
        catalog=catalog,
    )

    print(f"\n{'=' * 60}")
    print("Call-phase prompt:")
    print("=" * 60)
    print(pack.prompt)
    print("\n--- Stats ---")
    print(f"Included items: {pack.stats.included_count}")
    print(f"Total candidates: {pack.stats.total_candidates}")


if __name__ == "__main__":
    main()

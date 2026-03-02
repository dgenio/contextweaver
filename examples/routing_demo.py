"""Routing engine demo.

Demonstrates building a tool catalog, constructing the routing DAG, and
running beam-search routing to find the most relevant tools for a query.
"""

from __future__ import annotations

from contextweaver.routing.cards import cards_for_route, format_card_for_prompt
from contextweaver.routing.catalog import Catalog
from contextweaver.routing.router import Router, RouteResult
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import SelectableItem


def _build_catalog() -> Catalog:
    catalog = Catalog()
    tools = [
        SelectableItem(
            "db_read",
            "tool",
            "read_database",
            "Read records from the SQL database",
            tags=["data", "read"],
            namespace="db",
        ),
        SelectableItem(
            "db_write",
            "tool",
            "write_database",
            "Write records to the SQL database",
            tags=["data", "write"],
            namespace="db",
            side_effects=True,
        ),
        SelectableItem(
            "db_query",
            "tool",
            "query_database",
            "Execute arbitrary SQL queries",
            tags=["data", "sql"],
            namespace="db",
        ),
        SelectableItem(
            "send_email",
            "tool",
            "send_email",
            "Send an email notification",
            tags=["comm", "email"],
            namespace="comms",
            side_effects=True,
        ),
        SelectableItem(
            "search_web",
            "tool",
            "web_search",
            "Search the web for information",
            tags=["search", "web"],
            namespace="web",
        ),
        SelectableItem(
            "embed_text",
            "tool",
            "text_embedder",
            "Generate text embeddings using an LLM",
            tags=["ml", "embed"],
            namespace="ml",
        ),
        SelectableItem(
            "classify",
            "tool",
            "text_classifier",
            "Classify text into predefined categories",
            tags=["ml", "classify"],
            namespace="ml",
        ),
    ]
    for tool in tools:
        catalog.register(tool)
    return catalog


def main() -> None:
    catalog = _build_catalog()
    items = catalog.all()
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items, beam_width=3)

    queries = [
        "I need to read some database records",
        "Send a notification to the user",
        "Find similar documents using embeddings",
    ]

    for query in queries:
        print(f"\n{'=' * 60}")
        print(f"Query: {query!r}")
        result: RouteResult = router.route(query)
        print(f"Top candidates: {result.candidate_ids[:3]}")
        cards = cards_for_route(result.candidate_ids, catalog)
        if cards:
            print("Choice cards:")
            for card in cards:
                print(format_card_for_prompt(card))


if __name__ == "__main__":
    main()

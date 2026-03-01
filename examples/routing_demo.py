"""Routing engine demo.

Demonstrates the full routing pipeline:
  1. Load a tool catalog from JSON (or generate a sample one)
  2. Build a bounded choice graph via TreeBuilder
  3. Route user queries via beam-search Router
  4. Render compact choice cards for the LLM
  5. Show graph statistics

No external APIs required.
"""

from __future__ import annotations

import os
import sys

# Allow running from the repo root without installing.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from contextweaver.routing.cards import make_choice_cards, render_cards_text
from contextweaver.routing.catalog import (
    generate_sample_catalog,
    load_catalog_json,
)
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_CATALOG_FILE = os.path.join(_DATA_DIR, "sample_catalog.json")


def main() -> None:
    # ------------------------------------------------------------------ #
    # 1. Load tool catalog                                               #
    # ------------------------------------------------------------------ #
    print("=" * 60)
    print("TOOL CATALOG")
    print("=" * 60)

    items = load_catalog_json(_CATALOG_FILE)
    print(f"  Loaded {len(items)} tools from {os.path.basename(_CATALOG_FILE)}")

    # Show namespace distribution
    ns_counts: dict[str, int] = {}
    for it in items:
        ns = it.namespace or "(none)"
        ns_counts[ns] = ns_counts.get(ns, 0) + 1
    for ns, count in sorted(ns_counts.items()):
        print(f"    {ns:15s}: {count} tools")
    print()

    # ------------------------------------------------------------------ #
    # 2. Build routing graph                                             #
    # ------------------------------------------------------------------ #
    print("=" * 60)
    print("ROUTING GRAPH")
    print("=" * 60)

    builder = TreeBuilder(max_children=10)
    graph = builder.build(items)
    stats = graph.graph_stats()

    print(f"  Total items:           {stats['total_items']}")
    print(f"  Total nodes:           {stats['total_nodes']}")
    print(f"  Max depth:             {stats['max_depth']}")
    print(f"  Avg branching factor:  {stats['avg_branching_factor']}")
    print(f"  Max branching factor:  {stats['max_branching_factor']}")
    print(f"  Leaf nodes:            {stats['leaf_node_count']}")
    print(f"  Namespaces:            {stats['namespaces']}")
    print()

    # Show tree structure
    print("  Graph structure:")
    _print_tree(graph, graph.root_id, indent=4)
    print()

    # ------------------------------------------------------------------ #
    # 3. Route queries                                                   #
    # ------------------------------------------------------------------ #
    print("=" * 60)
    print("ROUTING QUERIES")
    print("=" * 60)

    router = Router(graph, beam_width=3, top_k=5)

    queries = [
        "I need to query the database for customer records",
        "Send an email notification to the team",
        "Search for documentation about the API",
        "Create a Jira ticket for the bug",
        "Run analytics on this quarter's revenue",
        "Classify this text using machine learning",
    ]

    for query in queries:
        print(f"\n  Query: {query!r}")
        result = router.route(query)

        if not result.candidate_items:
            print("    No candidates found.")
            continue

        # Build choice cards
        cards = make_choice_cards(
            result.candidate_items,
            scores=result.scores,
            max_choices=5,
        )

        # Render for the LLM
        cards_text = render_cards_text(cards)
        print(f"    Top {len(cards)} candidates:")
        for line in cards_text.splitlines():
            print(f"      {line}")

    print()

    # ------------------------------------------------------------------ #
    # 4. Show a generated sample catalog                                 #
    # ------------------------------------------------------------------ #
    print("=" * 60)
    print("GENERATED SAMPLE CATALOG (50 tools, 6+ namespaces)")
    print("=" * 60)

    sample = generate_sample_catalog(n=50, seed=42)
    print(f"  Generated {len(sample)} items")

    gen_ns: dict[str, int] = {}
    for entry in sample:
        ns = entry.get("namespace", "?")
        gen_ns[ns] = gen_ns.get(ns, 0) + 1
    for ns, count in sorted(gen_ns.items()):
        print(f"    {ns:15s}: {count} tools")


def _print_tree(graph, node_id: str, indent: int = 0) -> None:
    """Recursively print the choice graph tree structure."""
    if node_id not in graph.nodes:
        return
    node = graph.nodes[node_id]
    prefix = " " * indent
    child_nodes = sum(1 for c in node.children if node.child_types.get(c) == "node")
    child_items = sum(1 for c in node.children if node.child_types.get(c) == "item")
    print(f"{prefix}[{node_id}] {node.label} (nodes={child_nodes}, items={child_items})")

    for child_id in node.children:
        ct = node.child_types.get(child_id, "item")
        if ct == "node":
            _print_tree(graph, child_id, indent + 4)


if __name__ == "__main__":
    main()

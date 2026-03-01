"""Command-line interface for contextweaver.

Provides seven sub-commands:

demo        Run an end-to-end demonstration of both engines.
build       Build a routing graph from a catalog JSON file.
route       Route a query through a pre-built graph.
print-tree  Pretty-print the routing graph tree.
init        Create sample config + catalog in the current directory.
ingest      Ingest JSONL events into a session JSON file.
replay      Replay a session through the context engine.

This module is exempt from the 300-line limit.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# demo
# ---------------------------------------------------------------------------


def _cmd_demo(_args: argparse.Namespace) -> int:
    """Run an end-to-end demonstration of both engines."""
    from contextweaver.context.manager import ContextManager
    from contextweaver.routing.cards import make_choice_cards, render_cards_text
    from contextweaver.routing.catalog import generate_sample_catalog, load_catalog_dicts
    from contextweaver.routing.router import Router
    from contextweaver.routing.tree import TreeBuilder
    from contextweaver.types import ContextItem, ItemKind, Phase

    print("=" * 60)
    print("contextweaver demo -- Routing Engine + Context Engine")
    print("=" * 60)

    # --- Routing Engine ---
    print("\n--- Routing Engine ---\n")
    catalog_dicts = generate_sample_catalog(n=40, seed=7)
    items = load_catalog_dicts(catalog_dicts)
    print(f"Generated sample catalog: {len(items)} items")

    builder = TreeBuilder(max_children=10)
    graph = builder.build(items)
    stats = graph.graph_stats()
    print(f"Built graph: {stats['total_nodes']} nodes, depth={stats['max_depth']}")

    router = Router(graph, top_k=5)
    query = "search for customer invoices"
    result = router.route(query)
    print(f"\nQuery: {query!r}")
    print(f"Top {len(result.candidate_items)} candidates:")
    cards = make_choice_cards(result.candidate_items, scores=result.scores)
    print(render_cards_text(cards))

    # --- Context Engine ---
    print("\n--- Context Engine ---\n")
    mgr = ContextManager()
    mgr.ingest_sync(
        ContextItem(
            id="ut_001",
            kind=ItemKind.USER_TURN,
            text="Show me the latest invoices for Acme Corp.",
            token_estimate=12,
        )
    )
    mgr.ingest_sync(
        ContextItem(
            id="tc_001",
            kind=ItemKind.TOOL_CALL,
            text='invoices.search(customer="Acme Corp", status="all")',
            token_estimate=14,
            parent_id="ut_001",
        )
    )

    # Demonstrate the context firewall with a large tool result
    large_output = "Invoice data:\n" + "\n".join(
        f"INV-{i:04d} | Acme Corp | ${100 + i * 17:.2f} | 2025-01-{(i % 28) + 1:02d}"
        for i in range(100)
    )
    item, envelope = mgr.ingest_tool_result_sync(
        tool_call_id="tc_001",
        raw_output=large_output,
        tool_name="invoices.search",
    )
    print(f"Ingested tool result: {len(large_output)} chars")
    if item.artifact_ref:
        print(f"  Firewall triggered -> artifact_ref={item.artifact_ref}")
        print(f"  Summary: {item.text[:120]}...")
    else:
        print("  Passed through firewall (below threshold)")

    mgr.add_fact_sync("customer", "Acme Corp")
    mgr.add_episode_sync("ep_001", "User is investigating invoices for Acme Corp.")

    pack = mgr.build_sync("Answer the user about Acme Corp invoices", Phase.ANSWER)
    print("\nContext pack for ANSWER phase:")
    print(f"  Budget: {pack.budget_used}/{pack.budget_total} tokens")
    print(f"  Included items: {pack.stats.included_count}")
    print(f"  Dropped items: {pack.stats.dropped_count}")
    if pack.stats.dropped_reasons:
        print(f"  Drop reasons: {pack.stats.dropped_reasons}")
    if pack.artifacts_available:
        print(f"  Artifacts available: {pack.artifacts_available}")

    print("\n" + "=" * 60)
    print("demo complete")
    print("=" * 60)
    return 0


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def _cmd_build(args: argparse.Namespace) -> int:
    """Build a routing graph from a catalog JSON file."""
    from contextweaver.routing.catalog import load_catalog_json
    from contextweaver.routing.tree import TreeBuilder

    catalog_path = Path(args.catalog)
    if not catalog_path.exists():
        print(f"error: catalog file not found: {catalog_path}", file=sys.stderr)
        return 1

    items = load_catalog_json(catalog_path)
    print(f"Loaded {len(items)} items from {catalog_path}")

    builder = TreeBuilder(max_children=args.max_children)
    graph = builder.build(items)

    out_path = Path(args.out)
    graph.save(out_path)
    stats = graph.graph_stats()
    print(f"Saved graph to {out_path}")
    print(f"  Nodes: {stats['total_nodes']}")
    print(f"  Items: {stats['total_items']}")
    print(f"  Depth: {stats['max_depth']}")
    print(f"  Avg branching: {stats['avg_branching_factor']}")
    return 0


# ---------------------------------------------------------------------------
# route
# ---------------------------------------------------------------------------


def _cmd_route(args: argparse.Namespace) -> int:
    """Route a query through a pre-built graph."""
    from contextweaver.routing.cards import make_choice_cards, render_cards_text
    from contextweaver.routing.graph import ChoiceGraph
    from contextweaver.routing.router import Router

    graph_path = Path(args.graph)
    if not graph_path.exists():
        print(f"error: graph file not found: {graph_path}", file=sys.stderr)
        return 1

    graph = ChoiceGraph.load(graph_path)
    router = Router(graph, top_k=args.top_k)
    result = router.route(args.query, debug=True)

    print(f"Query: {args.query!r}")
    print(f"Top {len(result.candidate_items)} of {len(graph.items)} items:\n")

    cards = make_choice_cards(result.candidate_items, scores=result.scores)
    print(render_cards_text(cards))

    if result.debug_trace:
        print(f"\nDebug trace ({len(result.debug_trace)} steps):")
        for step in result.debug_trace[:5]:
            node = step["node"]
            scored = step["children_scored"][:3]
            top_children = ", ".join(f"{c['id']}={c['score']:.3f}" for c in scored)
            print(f"  depth={step['depth']} node={node} top=[{top_children}]")
    return 0


# ---------------------------------------------------------------------------
# print-tree
# ---------------------------------------------------------------------------


def _print_node(
    graph: Any,
    node_id: str,
    depth: int,
    max_depth: int,
    prefix: str = "",
    is_last: bool = True,
) -> None:
    """Recursively print one node of the graph tree."""
    if node_id not in graph.nodes:
        return
    node = graph.nodes[node_id]

    connector = "`-- " if is_last else "|-- "
    label = node.label or node_id
    n_children = len(node.children)
    print(f"{prefix}{connector}[{label}] ({n_children} children)")

    if depth >= max_depth:
        if n_children:
            child_prefix = prefix + ("    " if is_last else "|   ")
            print(f"{child_prefix}... (truncated at depth {max_depth})")
        return

    child_prefix = prefix + ("    " if is_last else "|   ")
    children = node.children
    for i, child_id in enumerate(children):
        child_is_last = i == len(children) - 1
        ct = node.child_types.get(child_id, "item")
        if ct == "node":
            _print_node(graph, child_id, depth + 1, max_depth, child_prefix, child_is_last)
        else:
            connector = "`-- " if child_is_last else "|-- "
            item = graph.items.get(child_id)
            name = item.name if item else child_id
            print(f"{child_prefix}{connector}{name}")


def _cmd_print_tree(args: argparse.Namespace) -> int:
    """Pretty-print the routing graph tree."""
    from contextweaver.routing.graph import ChoiceGraph

    graph_path = Path(args.graph)
    if not graph_path.exists():
        print(f"error: graph file not found: {graph_path}", file=sys.stderr)
        return 1

    graph = ChoiceGraph.load(graph_path)
    stats = graph.graph_stats()

    print(
        f"Graph: {stats['total_items']} items, {stats['total_nodes']} nodes, "
        f"depth={stats['max_depth']}"
    )
    print()
    _print_node(graph, graph.root_id, 0, args.depth)
    return 0


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def _cmd_init(_args: argparse.Namespace) -> int:
    """Create sample config + catalog in the current working directory."""
    from contextweaver.routing.catalog import generate_sample_catalog

    cwd = Path.cwd()
    catalog_path = cwd / "sample_catalog.json"
    config_path = cwd / "contextweaver_config.json"

    catalog_data = generate_sample_catalog(n=80, seed=42)
    catalog_path.write_text(json.dumps(catalog_data, indent=2) + "\n")
    print(f"Created {catalog_path} ({len(catalog_data)} items)")

    config: dict[str, Any] = {
        "budget": {"route": 2000, "call": 3000, "interpret": 4000, "answer": 6000},
        "scoring": {
            "recency_weight": 0.3,
            "tag_match_weight": 0.25,
            "kind_priority_weight": 0.35,
            "token_cost_penalty": 0.1,
        },
        "routing": {"max_children": 20, "beam_width": 2, "top_k": 10},
    }
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    print(f"Created {config_path}")

    print("\nNext steps:")
    print(f"  contextweaver build --catalog {catalog_path.name} --out graph.json")
    print("  contextweaver route --graph graph.json --query 'search invoices'")
    return 0


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------


def _cmd_ingest(args: argparse.Namespace) -> int:
    """Ingest JSONL events into a session JSON file."""
    from contextweaver.adapters.mcp import load_mcp_session_jsonl
    from contextweaver.context.manager import ContextManager
    from contextweaver.types import ItemKind

    events_path = Path(args.events)
    if not events_path.exists():
        print(f"error: events file not found: {events_path}", file=sys.stderr)
        return 1

    items = load_mcp_session_jsonl(events_path)
    print(f"Loaded {len(items)} events from {events_path}")

    mgr = ContextManager()
    firewall_count = 0

    for item in items:
        if item.kind == ItemKind.TOOL_RESULT and len(item.text) > 2000:
            # Apply firewall for large tool results
            fw_item, envelope = mgr.ingest_tool_result_sync(
                tool_call_id=item.parent_id or item.id,
                raw_output=item.text,
                tool_name=item.metadata.get("tool_name", "unknown"),
            )
            firewall_count += 1
            print(
                f"  Firewall: {item.id} ({len(item.text)} chars -> "
                f"{len(fw_item.text)} chars summary)"
            )
        else:
            mgr.ingest_sync(item)

    # Serialise session
    all_items = mgr.event_log.all_sync()
    session: dict[str, Any] = {
        "source": str(events_path),
        "item_count": len(all_items),
        "firewall_count": firewall_count,
        "items": [it.to_dict() for it in all_items],
        "artifact_refs": mgr.artifact_store.list_refs(),
    }
    out_path = Path(args.out)
    out_path.write_text(json.dumps(session, indent=2) + "\n")
    print(f"Saved session to {out_path} ({len(all_items)} items, {firewall_count} firewalled)")
    return 0


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


def _cmd_replay(args: argparse.Namespace) -> int:
    """Replay a session through the context engine."""
    from contextweaver.context.manager import ContextManager
    from contextweaver.types import ContextItem, Phase

    session_path = Path(args.session)
    if not session_path.exists():
        print(f"error: session file not found: {session_path}", file=sys.stderr)
        return 1

    with open(session_path) as f:
        session = json.load(f)

    raw_items = session.get("items", [])
    items = [ContextItem.from_dict(d) for d in raw_items]
    print(f"Loaded session: {len(items)} items from {session_path}")

    mgr = ContextManager()
    for item in items:
        mgr.ingest_sync(item)

    phase = Phase(args.phase)
    pack = mgr.build_sync(
        goal=f"Replay session in {phase.value} phase",
        phase=phase,
        budget_tokens=args.budget,
    )

    print(f"\nReplay results ({phase.value} phase, budget={args.budget}):")
    print(f"  Budget used: {pack.budget_used}/{pack.budget_total} tokens")
    print(f"  Included: {pack.stats.included_count}")
    print(f"  Dropped: {pack.stats.dropped_count}")
    if pack.stats.dropped_reasons:
        print(f"  Drop reasons: {pack.stats.dropped_reasons}")
    print(f"  Dedup removed: {pack.stats.dedup_removed}")
    if pack.artifacts_available:
        print(f"  Artifacts: {pack.artifacts_available}")

    if args.preview:
        text = pack.rendered_text
        if len(text) > 500 and not args.full:
            text = text[:500] + "\n... (truncated, use --full to see all)"
        print(f"\n--- Rendered Context ---\n{text}")

    if args.full:
        print(f"\n--- Full Rendered Context ---\n{pack.rendered_text}")

    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="contextweaver",
        description="Dynamic context management for tool-using AI agents.",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    # demo
    sub.add_parser("demo", help="Run an end-to-end demonstration of both engines.")

    # build
    p_build = sub.add_parser(
        "build",
        help="Build a routing graph from a catalog JSON file.",
    )
    p_build.add_argument(
        "--catalog",
        required=True,
        help="Path to the catalog JSON file.",
    )
    p_build.add_argument(
        "--out",
        default="graph.json",
        help="Output graph file (default: graph.json).",
    )
    p_build.add_argument(
        "--max-children",
        type=int,
        default=20,
        help="Max children per node (default: 20).",
    )

    # route
    p_route = sub.add_parser(
        "route",
        help="Route a query through a pre-built graph.",
    )
    p_route.add_argument(
        "--graph",
        required=True,
        help="Path to the graph JSON file.",
    )
    p_route.add_argument(
        "--query",
        required=True,
        help="The user query to route.",
    )
    p_route.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Number of top results (default: 10).",
    )

    # print-tree
    p_tree = sub.add_parser(
        "print-tree",
        help="Pretty-print the routing graph tree.",
    )
    p_tree.add_argument(
        "--graph",
        required=True,
        help="Path to the graph JSON file.",
    )
    p_tree.add_argument(
        "--depth",
        type=int,
        default=3,
        help="Max display depth (default: 3).",
    )

    # init
    sub.add_parser("init", help="Create sample config + catalog in cwd.")

    # ingest
    p_ingest = sub.add_parser(
        "ingest",
        help="Ingest JSONL events into a session JSON file.",
    )
    p_ingest.add_argument(
        "--events",
        required=True,
        help="Path to the JSONL events file.",
    )
    p_ingest.add_argument(
        "--out",
        default="session.json",
        help="Output session file (default: session.json).",
    )

    # replay
    p_replay = sub.add_parser(
        "replay",
        help="Replay a session through the context engine.",
    )
    p_replay.add_argument(
        "--session",
        required=True,
        help="Path to the session JSON file.",
    )
    p_replay.add_argument(
        "--phase",
        default="answer",
        choices=["route", "call", "interpret", "answer"],
        help="Execution phase (default: answer).",
    )
    p_replay.add_argument(
        "--budget",
        type=int,
        default=4000,
        help="Token budget (default: 4000).",
    )
    p_replay.add_argument(
        "--preview",
        action="store_true",
        help="Print a truncated preview of the rendered context.",
    )
    p_replay.add_argument(
        "--full",
        action="store_true",
        help="Print the full rendered context.",
    )

    return parser


_HANDLERS: dict[str, Any] = {
    "demo": _cmd_demo,
    "build": _cmd_build,
    "route": _cmd_route,
    "print-tree": _cmd_print_tree,
    "init": _cmd_init,
    "ingest": _cmd_ingest,
    "replay": _cmd_replay,
}


def main() -> None:
    """Entry point for the ``contextweaver`` CLI."""
    parser = _build_parser()
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(0)
    handler = _HANDLERS.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)
    sys.exit(handler(args))


if __name__ == "__main__":
    main()

"""Command-line interface for contextweaver.

Provides seven sub-commands:

demo        Run a built-in demonstration of both engines.
build       Build a routing graph from a catalog JSON file.
route       Route a query over a pre-built routing graph.
print-tree  Pretty-print the routing tree for a graph.
init        Scaffold contextweaver config + sample catalog in cwd.
ingest      Ingest a JSONL session into a serialised session file.
replay      Replay a session and build context for a given phase.

Invocable as ``python -m contextweaver`` or ``contextweaver`` (via
``[project.scripts]``).  Exempt from 300-line module limit.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from contextweaver.config import ContextBudget
from contextweaver.context.manager import ContextManager
from contextweaver.routing.cards import make_choice_cards, render_cards_text
from contextweaver.routing.catalog import Catalog, generate_sample_catalog, load_catalog_json
from contextweaver.routing.graph_io import load_graph, save_graph
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import ContextItem, ItemKind, Phase, SelectableItem

# ---------------------------------------------------------------------------
# JSON-L session helpers
# ---------------------------------------------------------------------------

_KIND_MAP: dict[str, ItemKind] = {
    "user_turn": ItemKind.user_turn,
    "agent_msg": ItemKind.agent_msg,
    "tool_call": ItemKind.tool_call,
    "tool_result": ItemKind.tool_result,
    "doc_snippet": ItemKind.doc_snippet,
    "memory_fact": ItemKind.memory_fact,
    "plan_state": ItemKind.plan_state,
    "policy": ItemKind.policy,
}


def _load_jsonl(path: str) -> list[ContextItem]:
    """Read a JSONL file and convert each line into a ContextItem."""
    items: list[ContextItem] = []
    for lineno, line in enumerate(Path(path).read_text(encoding="utf-8").strip().splitlines(), 1):
        obj: dict[str, Any] = json.loads(line)
        kind = _KIND_MAP.get(obj.get("type", "user_turn"), ItemKind.user_turn)
        text = obj.get("text") or obj.get("content", "")
        items.append(
            ContextItem(
                id=obj.get("id", f"line-{lineno}"),
                kind=kind,
                text=str(text),
                metadata={
                    k: v
                    for k, v in obj.items()
                    if k not in {"id", "type", "text", "content", "parent_id", "token_estimate"}
                },
                parent_id=obj.get("parent_id"),
                token_estimate=int(obj.get("token_estimate", 0)),
            )
        )
    return items


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def _cmd_demo(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Run a built-in demonstration of both engines."""
    print("=" * 60)
    print("contextweaver demo — end-to-end demonstration")
    print("=" * 60)

    # 1. Build a sample catalog
    raw_items = generate_sample_catalog(n=40, seed=42)
    catalog = Catalog()
    for raw in raw_items:
        catalog.register(SelectableItem.from_dict(raw))
    items = catalog.all()
    ns_count = len({it.namespace for it in items})
    print(f"\n[1/5] Loaded catalog: {len(items)} items across {ns_count} namespaces")

    # 2. Build routing graph
    builder = TreeBuilder(max_children=10)
    graph = builder.build(items)
    stats = graph.stats()
    print(f"[2/5] Built routing graph: {stats['total_nodes']} nodes, depth={stats['max_depth']}")

    # 3. Route a query
    router = Router(graph, items=items, beam_width=3, top_k=5)
    query = "find unpaid invoices and send a reminder email"
    result = router.route(query)
    print(f"[3/5] Routed query: {query!r}")
    print(f"      Top candidates: {result.candidate_ids}")
    cards = make_choice_cards(
        result.candidate_items,
        scores=dict(zip(result.candidate_ids, result.scores, strict=False)),
    )
    print(f"      Choice cards ({len(cards)}):")
    print(render_cards_text(cards))

    # 4. Ingest sample events and build context
    mgr = ContextManager()
    mgr.ingest(
        ContextItem(id="u1", kind=ItemKind.user_turn, text="How many open invoices do we have?")
    )
    mgr.ingest(
        ContextItem(id="a1", kind=ItemKind.agent_msg, text="Let me check the billing system.")
    )
    mgr.ingest(
        ContextItem(
            id="tc1", kind=ItemKind.tool_call, text="invoices.search(status='open')", parent_id="u1"
        )
    )
    mgr.ingest(
        ContextItem(
            id="tr1",
            kind=ItemKind.tool_result,
            text=(
                "invoice_id: INV-001\nstatus: open\namount: 5000\n\n"
                "invoice_id: INV-002\nstatus: open\namount: 3200\n\n"
                "summary: 2 open invoices, total $8,200"
            ),
            parent_id="tc1",
        )
    )
    mgr.add_fact("customer_tier", "enterprise")
    mgr.add_episode("ep-prev", "Previously discussed payment terms with client")

    pack = mgr.build_sync(phase=Phase.answer, query="open invoices")
    print(f"\n[4/5] Built context pack: phase={pack.phase.value}")
    print(f"      Candidates: {pack.stats.total_candidates}, Included: {pack.stats.included_count}")
    print(
        f"      Dedup removed: {pack.stats.dedup_removed},"
        f" Closures: {pack.stats.dependency_closures}"
    )
    print(f"      Token breakdown: {pack.stats.tokens_per_section}")

    # 5. Show prompt preview
    preview = pack.prompt[:400]
    print(f"\n[5/5] Prompt preview ({len(pack.prompt)} chars total):")
    print(preview)
    if len(pack.prompt) > 400:
        print("      ...")

    print("\n" + "=" * 60)
    print("Demo complete.")
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    """Build a routing graph from a catalog JSON file."""
    catalog_path: str = args.catalog
    out_path: str = args.out
    max_children: int = args.max_children

    items = load_catalog_json(catalog_path)
    print(f"Loaded {len(items)} items from {catalog_path}")

    builder = TreeBuilder(max_children=max_children)
    graph = builder.build(items)

    save_graph(graph, out_path)
    stats = graph.stats()
    print(f"Graph saved to {out_path}")
    print(f"Stats: {json.dumps(stats, indent=2)}")
    return 0


def _cmd_route(args: argparse.Namespace) -> int:
    """Route a query over a pre-built graph."""
    graph_path: str = args.graph
    catalog_path: str = args.catalog
    query: str = args.query
    top_k: int = args.top_k

    graph = load_graph(graph_path)
    all_items = load_catalog_json(catalog_path)

    # Keep only items present in the graph
    graph_item_ids = set(graph.items())
    items_list = [it for it in all_items if it.id in graph_item_ids]

    router = Router(graph, items=items_list, beam_width=3, top_k=top_k)
    result = router.route(query)

    print(f"Query: {query!r}")
    print(f"Results ({len(result.candidate_ids)}):")
    cards = make_choice_cards(
        result.candidate_items,
        scores=dict(zip(result.candidate_ids, result.scores, strict=False)),
    )
    print(render_cards_text(cards))
    return 0


def _cmd_print_tree(args: argparse.Namespace) -> int:
    """Pretty-print the routing tree for a graph."""
    graph_path: str = args.graph
    max_depth: int = args.depth

    graph = load_graph(graph_path)

    def _print_node(node_id: str, depth: int, prefix: str = "") -> None:
        if depth > max_depth:
            return
        node = graph.get_node(node_id)
        is_item = node_id in set(graph.items())
        marker = "*" if is_item else ">"
        label = node.label or node_id
        hint = f" - {node.routing_hint}" if node.routing_hint and not is_item else ""
        print(f"{prefix}{marker} {label}{hint}")
        children = graph.successors(node_id)
        for i, child in enumerate(children):
            last = i == len(children) - 1
            child_prefix = prefix + ("    " if last else "|   ")
            _print_node(child, depth + 1, child_prefix)

    print(f"Routing tree (depth={max_depth}):")
    _print_node(graph.root_id, 0)
    stats = graph.stats()
    print(
        f"\nStats: {stats['total_nodes']} nodes,"
        f" {stats['total_items']} items,"
        f" depth={stats['max_depth']}"
    )
    return 0


def _cmd_init(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Scaffold contextweaver config + sample catalog in cwd."""
    config = {
        "version": "0.1.0",
        "budget": {"route": 2000, "call": 3000, "interpret": 4000, "answer": 6000},
        "scoring": {
            "recency_weight": 0.3,
            "tag_match_weight": 0.25,
            "kind_priority_weight": 0.35,
            "token_cost_penalty": 0.1,
        },
        "policy": {"ttl_behavior": "drop", "sensitivity_floor": "confidential"},
        "routing": {"max_children": 20, "beam_width": 2, "top_k": 20, "confidence_gap": 0.15},
    }
    config_path = Path("contextweaver.json")
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"Created {config_path}")

    catalog_path = Path("sample_catalog.json")
    raw_items = generate_sample_catalog(n=40, seed=42)
    catalog_path.write_text(json.dumps(raw_items, indent=2) + "\n", encoding="utf-8")
    print(f"Created {catalog_path} ({len(raw_items)} items)")
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    """Ingest a JSONL session into a serialised session file."""
    events_path: str = args.events
    out_path: str = args.out

    items = _load_jsonl(events_path)
    mgr = ContextManager()

    firewall_count = 0
    kind_counts: dict[str, int] = {}
    for item in items:
        kind_counts[item.kind.value] = kind_counts.get(item.kind.value, 0) + 1
        if item.kind == ItemKind.tool_result and len(item.text) > 2000:
            _, envelope = mgr.ingest_tool_result(
                tool_call_id=item.parent_id or item.id,
                raw_output=item.text,
                tool_name=str(item.metadata.get("tool_name", "")),
            )
            for i, fact in enumerate(envelope.facts):
                mgr.add_fact(f"{item.id}:fact:{i}", fact)
            firewall_count += 1
        else:
            mgr.ingest(item)

    # Serialize session
    session: dict[str, Any] = {
        "event_count": len(items),
        "events": [it.to_dict() for it in mgr.event_log.all()],
        "artifacts": {
            ref.handle: {
                "media_type": ref.media_type,
                "size_bytes": ref.size_bytes,
                "label": ref.label,
            }
            for ref in mgr.artifact_store.list_refs()
        },
        "facts": {f.key: f.value for f in mgr.fact_store.all()},
        "episodes": [
            {"episode_id": ep.episode_id, "summary": ep.summary} for ep in mgr.episodic_store.all()
        ],
    }
    Path(out_path).write_text(json.dumps(session, indent=2) + "\n", encoding="utf-8")

    print(f"Ingested {len(items)} events from {events_path}")
    print(f"Event counts: {json.dumps(kind_counts)}")
    print(f"Firewall triggers: {firewall_count}")
    print(f"Artifacts stored: {len(session['artifacts'])}")
    print(f"Session saved to {out_path}")
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    """Replay a session and build context for a given phase."""
    session_path: str = args.session
    phase_str: str = args.phase
    budget_tokens: int = args.budget
    preview: bool = not args.full

    session: dict[str, Any] = json.loads(Path(session_path).read_text(encoding="utf-8"))

    # Re-ingest events
    mgr = ContextManager(
        budget=ContextBudget(
            route=budget_tokens,
            call=budget_tokens,
            interpret=budget_tokens,
            answer=budget_tokens,
        )
    )
    for raw_event in session.get("events", []):
        item = ContextItem.from_dict(raw_event)
        mgr.ingest(item)

    # Restore facts
    for key, value in session.get("facts", {}).items():
        mgr.add_fact(key, value)

    # Restore episodes
    for ep in session.get("episodes", []):
        mgr.add_episode(ep["episode_id"], ep["summary"])

    phase = Phase(phase_str)
    pack = mgr.build_sync(phase=phase, query="replay", budget_tokens=budget_tokens)

    print(f"=== Context Build: phase={phase.value}, budget={budget_tokens} ===")
    print(
        f"Stats: total_candidates={pack.stats.total_candidates}, "
        f"included={pack.stats.included_count}, "
        f"dropped={pack.stats.dropped_count} ({pack.stats.dropped_reasons}), "
        f"dedup={pack.stats.dedup_removed}, "
        f"closures={pack.stats.dependency_closures}"
    )
    print(f"Token breakdown: {pack.stats.tokens_per_section}")
    total_tokens = sum(pack.stats.tokens_per_section.values()) + pack.stats.header_footer_tokens
    print(f"Total tokens: {total_tokens} / {budget_tokens}")

    artifacts = session.get("artifacts", {})
    if artifacts:
        print(f"Artifacts available: {list(artifacts.keys())}")

    facts = session.get("facts", {})
    if facts:
        fact_strs = [f"{k}={v}" for k, v in facts.items()]
        print(f"Facts: {fact_strs}")

    print("--- Rendered prompt ---")
    if preview:
        print(pack.prompt[:500])
        if len(pack.prompt) > 500:
            print(f"... ({len(pack.prompt) - 500} more chars, use --full to see all)")
    else:
        print(pack.prompt)
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="contextweaver",
        description="Dynamic context management for tool-using AI agents.",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    # demo
    sub.add_parser("demo", help="Run a built-in demonstration of both engines.")

    # build
    p_build = sub.add_parser("build", help="Build a routing graph from a catalog.")
    p_build.add_argument("--catalog", required=True, help="Path to the tool catalog JSON file.")
    p_build.add_argument("--out", required=True, help="Output path for the graph JSON file.")
    p_build.add_argument(
        "--max-children", type=int, default=20, help="Max children per node (default: 20)."
    )

    # route
    p_route = sub.add_parser("route", help="Route a query over a pre-built graph.")
    p_route.add_argument("--graph", required=True, help="Path to the graph JSON file.")
    p_route.add_argument("--catalog", required=True, help="Path to the catalog JSON file.")
    p_route.add_argument("--query", required=True, help="The user query to route.")
    p_route.add_argument("--top-k", type=int, default=10, help="Max results (default: 10).")

    # print-tree
    p_tree = sub.add_parser("print-tree", help="Pretty-print the routing tree.")
    p_tree.add_argument("--graph", required=True, help="Path to the graph JSON file.")
    p_tree.add_argument("--depth", type=int, default=3, help="Max depth to display (default: 3).")

    # init
    sub.add_parser("init", help="Scaffold contextweaver config + sample catalog in cwd.")

    # ingest
    p_ingest = sub.add_parser("ingest", help="Ingest a JSONL session file.")
    p_ingest.add_argument("--events", required=True, help="Path to the JSONL session file.")
    p_ingest.add_argument("--out", required=True, help="Output path for the session JSON file.")

    # replay
    p_replay = sub.add_parser("replay", help="Replay a session and build context.")
    p_replay.add_argument("--session", required=True, help="Path to the session JSON file.")
    p_replay.add_argument(
        "--phase",
        default="answer",
        choices=["route", "call", "interpret", "answer"],
        help="Phase (default: answer).",
    )
    p_replay.add_argument("--budget", type=int, default=4000, help="Token budget (default: 4000).")
    p_replay.add_argument("--full", action="store_true", default=False, help="Show full prompt.")

    return parser


_HANDLERS = {
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
    try:
        sys.exit(handler(args))
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(f"Error: invalid JSON — {exc}", file=sys.stderr)
        sys.exit(1)
    except (ValueError, PermissionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

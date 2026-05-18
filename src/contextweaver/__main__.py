"""Command-line interface for contextweaver.

Provides nine sub-commands:

demo        Run a built-in demonstration of both engines.
build       Build a routing graph from a catalog JSON file.
route       Route a query over a pre-built routing graph.
print-tree  Pretty-print the routing tree for a graph.
init        Scaffold contextweaver config + sample catalog in cwd.
ingest      Ingest a JSONL session into a serialised session file.
replay      Replay a session and build context for a given phase.
stats       Render a human-readable :class:`BuildStats` diagnostic report
            from an ingested session (issue #106).
budget-check
            Assert an ingested session's rendered prompt stays under a
            token ceiling for CI regression checks (issue #276).

Invocable as ``python -m contextweaver`` or ``contextweaver`` (via
``[project.scripts]``).  Exempt from the 300-line module limit.

Built on `Typer <https://typer.tiangolo.com>`_ + `Rich <https://rich.readthedocs.io>`_
(both core dependencies as of v0.5; the legacy ``[cli]`` extra is kept as an
empty alias for one cycle).  Issue #221.
"""

from __future__ import annotations

import json
import sys
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.tree import Tree as RichTree

from contextweaver.config import ContextBudget
from contextweaver.context.manager import ContextManager
from contextweaver.routing.cards import make_choice_cards, render_cards_text
from contextweaver.routing.catalog import Catalog, generate_sample_catalog, load_catalog_json
from contextweaver.routing.graph_io import load_graph, save_graph
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import ContextItem, ItemKind, Phase, SelectableItem

# ---------------------------------------------------------------------------
# Typer app + global console
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="contextweaver",
    help="Dynamic context management for tool-using AI agents.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)

_console = Console()


# Typer prefers ``str`` Enums for ``--phase``-style choice flags over Click's
# ``Choice`` because the Enum members produce typed values inside the handler
# (and Typer auto-renders them as ``--phase [route|call|interpret|answer]``
# in ``--help`` output).
class _PhaseChoice(str, Enum):
    route = "route"
    call = "call"
    interpret = "interpret"
    answer = "answer"


class _StatsFormatChoice(str, Enum):
    rich = "rich"
    text = "text"


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
        try:
            obj: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno}: invalid JSON — {exc}") from exc
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


def _restore_manager_from_session(
    session_path: str, budget_tokens: int | None = None
) -> ContextManager:
    """Rebuild a :class:`ContextManager` from a session JSON written by ``ingest``."""
    session: dict[str, Any] = json.loads(Path(session_path).read_text(encoding="utf-8"))
    if budget_tokens is None:
        mgr = ContextManager()
    else:
        mgr = ContextManager(
            budget=ContextBudget(
                route=budget_tokens,
                call=budget_tokens,
                interpret=budget_tokens,
                answer=budget_tokens,
            )
        )
    for idx, raw_event in enumerate(session.get("events", []), 1):
        try:
            item = ContextItem.from_dict(raw_event)
        except Exception as exc:
            raise ValueError(f"{session_path}: session event {idx}: {exc}") from exc
        mgr.ingest(item)
    for key, value in session.get("facts", {}).items():
        mgr.add_fact(key, value)
    for ep in session.get("episodes", []):
        mgr.add_episode(ep["episode_id"], ep["summary"])
    return mgr


def _write_budget_baseline(
    path: Path,
    *,
    phase: str,
    query: str,
    max_tokens: int,
    prompt_tokens: int,
    tokens_per_section: dict[str, int],
) -> None:
    """Write a deterministic budget baseline payload for ``budget-check --ratchet``."""
    payload = {
        "version": 1,
        "phase": phase,
        "query": query,
        "max_tokens": max_tokens,
        "prompt_tokens": prompt_tokens,
        "tokens_per_section": dict(sorted(tokens_per_section.items())),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


@app.command()
def demo() -> None:
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


@app.command()
def build(
    catalog: Annotated[Path, typer.Option(..., help="Path to the tool catalog JSON file.")],
    out: Annotated[Path, typer.Option(..., help="Output path for the graph JSON file.")],
    max_children: Annotated[
        int, typer.Option("--max-children", help="Max children per node.")
    ] = 20,
) -> None:
    """Build a routing graph from a catalog JSON file."""
    items = load_catalog_json(str(catalog))
    print(f"Loaded {len(items)} items from {catalog}")
    builder = TreeBuilder(max_children=max_children)
    graph = builder.build(items)
    save_graph(graph, str(out))
    stats = graph.stats()
    print(f"Graph saved to {out}")
    print(f"Stats: {json.dumps(stats, indent=2)}")


@app.command()
def route(
    graph: Annotated[Path, typer.Option(..., help="Path to the graph JSON file.")],
    catalog: Annotated[Path, typer.Option(..., help="Path to the catalog JSON file.")],
    query: Annotated[str, typer.Option(..., help="The user query to route.")],
    top_k: Annotated[int, typer.Option("--top-k", help="Max results.")] = 10,
    beam_width: Annotated[int, typer.Option("--beam-width", help="Beam width.")] = 3,
) -> None:
    """Route a query over a pre-built routing graph."""
    graph_obj = load_graph(str(graph))
    all_items = load_catalog_json(str(catalog))
    graph_item_ids = set(graph_obj.items())
    items_list = [it for it in all_items if it.id in graph_item_ids]

    router = Router(graph_obj, items=items_list, beam_width=beam_width, top_k=top_k)
    result = router.route(query)

    print(f"Query: {query!r}")
    print(f"Results ({len(result.candidate_ids)}):")
    cards = make_choice_cards(
        result.candidate_items,
        scores=dict(zip(result.candidate_ids, result.scores, strict=False)),
    )
    print(render_cards_text(cards))


@app.command("print-tree")
def print_tree(
    graph: Annotated[Path, typer.Option(..., help="Path to the graph JSON file.")],
    depth: Annotated[int, typer.Option(help="Max depth to display.")] = 3,
) -> None:
    """Pretty-print the routing tree for a graph (Rich-formatted)."""
    graph_obj = load_graph(str(graph))
    items_set = set(graph_obj.items())

    def _build(node_id: str, cur_depth: int) -> RichTree:
        node = graph_obj.get_node(node_id)
        is_item = node_id in items_set
        label = node.label or node_id
        if is_item:
            rendered = f"[bold green]* {label}[/bold green]"
        else:
            hint = f"  [dim]({node.routing_hint})[/dim]" if node.routing_hint else ""
            rendered = f"[bold cyan]> {label}[/bold cyan]{hint}"
        tree = RichTree(rendered)
        if cur_depth < depth:
            for child in graph_obj.successors(node_id):
                tree.add(_build(child, cur_depth + 1))
        return tree

    _console.print(f"[bold]Routing tree (depth={depth}):[/bold]")
    _console.print(_build(graph_obj.root_id, 0))
    stats = graph_obj.stats()
    _console.print(
        f"\n[dim]Stats: {stats['total_nodes']} nodes,"
        f" {stats['total_items']} items,"
        f" depth={stats['max_depth']}[/dim]"
    )


@app.command()
def init(
    force: Annotated[bool, typer.Option(help="Overwrite existing files.")] = False,
) -> None:
    """Scaffold contextweaver config + sample catalog in cwd."""
    config_path = Path("contextweaver.json")
    catalog_path = Path("sample_catalog.json")

    existing = [p for p in (config_path, catalog_path) if p.exists()]
    if existing and not force:
        names = ", ".join(str(p) for p in existing)
        print(f"Error: {names} already exist. Use --force to overwrite.", file=sys.stderr)
        raise typer.Exit(1)

    config = {
        "version": "0.1.0",
        "budget": {"route": 2000, "call": 3000, "interpret": 4000, "answer": 6000},
        "scoring": {
            "recency_weight": 0.3,
            "tag_match_weight": 0.25,
            "kind_priority_weight": 0.35,
            "token_cost_penalty": 0.1,
        },
        "policy": {"sensitivity_floor": "confidential"},
        "routing": {"max_children": 20, "beam_width": 2, "top_k": 20, "confidence_gap": 0.15},
    }
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"Created {config_path}")

    raw_items = generate_sample_catalog(n=40, seed=42)
    catalog_path.write_text(json.dumps(raw_items, indent=2) + "\n", encoding="utf-8")
    print(f"Created {catalog_path} ({len(raw_items)} items)")


@app.command()
def ingest(
    events: Annotated[Path, typer.Option(..., help="Path to the JSONL session file.")],
    out: Annotated[Path, typer.Option(..., help="Output path for the session JSON file.")],
) -> None:
    """Ingest a JSONL session into a serialised session file."""
    items = _load_jsonl(str(events))
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
    Path(out).write_text(json.dumps(session, indent=2) + "\n", encoding="utf-8")

    print(f"Ingested {len(items)} events from {events}")
    print(f"Event counts: {json.dumps(kind_counts)}")
    print(f"Firewall triggers: {firewall_count}")
    print(f"Artifacts stored: {len(session['artifacts'])}")
    print(f"Session saved to {out}")


@app.command()
def replay(
    session: Annotated[Path, typer.Option(..., help="Path to the session JSON file.")],
    phase: Annotated[_PhaseChoice, typer.Option(help="Phase to render.")] = _PhaseChoice.answer,
    budget: Annotated[int, typer.Option(help="Token budget.")] = 4000,
    full: Annotated[bool, typer.Option(help="Show full prompt instead of preview.")] = False,
) -> None:
    """Replay a session and build context for a given phase."""
    mgr = _restore_manager_from_session(str(session), budget)
    pack = mgr.build_sync(phase=Phase(phase.value), query="replay", budget_tokens=budget)

    print(f"=== Context Build: phase={phase.value}, budget={budget} ===")
    print(
        f"Stats: total_candidates={pack.stats.total_candidates}, "
        f"included={pack.stats.included_count}, "
        f"dropped={pack.stats.dropped_count} ({pack.stats.dropped_reasons}), "
        f"dedup={pack.stats.dedup_removed}, "
        f"closures={pack.stats.dependency_closures}"
    )
    print(f"Token breakdown: {pack.stats.tokens_per_section}")
    print(f"Total tokens: {pack.stats.prompt_tokens} / {budget}")

    raw_session: dict[str, Any] = json.loads(Path(session).read_text(encoding="utf-8"))
    artifacts = raw_session.get("artifacts", {})
    if artifacts:
        print(f"Artifacts available: {list(artifacts.keys())}")
    facts = raw_session.get("facts", {})
    if facts:
        print(f"Facts: {[f'{k}={v}' for k, v in facts.items()]}")

    print("--- Rendered prompt ---")
    if full:
        print(pack.prompt)
    else:
        print(pack.prompt[:500])
        if len(pack.prompt) > 500:
            print(f"... ({len(pack.prompt) - 500} more chars, use --full to see all)")


@app.command()
def stats(
    session: Annotated[Path, typer.Option(..., help="Path to the session JSON file.")],
    phase: Annotated[
        _PhaseChoice, typer.Option(help="Phase to render the report for.")
    ] = _PhaseChoice.answer,
    budget: Annotated[int, typer.Option(help="Token budget for the build.")] = 4000,
    format: Annotated[  # noqa: A002 — Typer CLI flag name
        _StatsFormatChoice,
        typer.Option(
            "--format",
            help="Output format: rich renders panels and tables; text is grep-friendly.",
        ),
    ] = _StatsFormatChoice.rich,
) -> None:
    """Render a human-readable :class:`BuildStats` diagnostic report (issue #106)."""
    mgr = _restore_manager_from_session(str(session), budget)
    pack = mgr.build_sync(phase=Phase(phase.value), query="stats", budget_tokens=budget)
    if format == _StatsFormatChoice.rich:
        _console.print(pack.stats.report(format="rich", phase=phase.value, budget=budget))
    else:
        print(pack.stats.report(format="text", phase=phase.value, budget=budget))


@app.command("budget-check")
def budget_check(
    session: Annotated[Path, typer.Option(..., help="Path to the session JSON file.")],
    max_tokens: Annotated[
        int, typer.Option("--max-tokens", help="Maximum allowed rendered prompt tokens.")
    ],
    phase: Annotated[
        _PhaseChoice, typer.Option(help="Phase to build before checking.")
    ] = _PhaseChoice.answer,
    query: Annotated[
        str, typer.Option(help="Query passed to ContextManager.build_sync().")
    ] = "budget-check",
    breakdown: Annotated[
        bool, typer.Option("--breakdown", help="Print per-section token usage.")
    ] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
    ratchet: Annotated[
        bool,
        typer.Option(
            "--ratchet",
            help=(
                "Read/write a baseline JSON file and fail if prompt token usage grows "
                "above the stored baseline."
            ),
        ),
    ] = False,
    ratchet_path: Annotated[
        Path,
        typer.Option(
            "--ratchet-path",
            help="Baseline JSON path used by --ratchet.",
        ),
    ] = Path(".budget-baseline.json"),
) -> None:
    """Fail CI when a rendered context build exceeds a token ceiling (issue #276)."""
    if max_tokens < 1:
        raise typer.BadParameter("--max-tokens must be greater than 0", param_hint="--max-tokens")
    if not session.exists():
        raise typer.BadParameter(f"session file not found: {session}", param_hint="--session")

    mgr = _restore_manager_from_session(str(session))
    pack = mgr.build_sync(phase=Phase(phase.value), query=query)
    total = pack.stats.prompt_tokens
    over = max(total - max_tokens, 0)
    utilization = total / max_tokens
    budget_ok = total <= max_tokens

    ratchet_ok = True
    ratchet_baseline: int | None = None
    ratchet_path_output: str | None = None
    ratchet_written = False
    if ratchet:
        ratchet_file = ratchet_path
        ratchet_path_output = str(ratchet_file)
        if ratchet_file.exists():
            baseline: dict[str, Any] = json.loads(ratchet_file.read_text(encoding="utf-8"))
            ratchet_baseline = int(baseline.get("prompt_tokens", 0))
            ratchet_ok = total <= ratchet_baseline
        if budget_ok and ratchet_ok:
            _write_budget_baseline(
                ratchet_file,
                phase=phase.value,
                query=query,
                max_tokens=max_tokens,
                prompt_tokens=total,
                tokens_per_section=pack.stats.tokens_per_section,
            )
            ratchet_written = True

    ok = budget_ok and ratchet_ok
    payload: dict[str, Any] = {
        "ok": ok,
        "phase": phase.value,
        "query": query,
        "prompt_tokens": total,
        "max_tokens": max_tokens,
        "utilization": round(utilization, 4),
        "over": over,
        "tokens_per_section": dict(sorted(pack.stats.tokens_per_section.items())),
        "ratchet": {
            "path": ratchet_path_output,
            "baseline_prompt_tokens": ratchet_baseline,
            "written": ratchet_written,
            "ok": ratchet_ok,
        },
    }

    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        if budget_ok:
            print(f"OK total={total} budget={max_tokens} utilization={utilization:.1%}")
        else:
            print(f"FAIL total={total} budget={max_tokens} over={over}")
        if ratchet:
            if ratchet_baseline is not None and not ratchet_ok:
                print(f"Ratchet failed: total={total} baseline={ratchet_baseline}")
            elif ratchet_written:
                print(f"Ratchet baseline written: {ratchet_path}")
        if breakdown:
            print("Token breakdown:")
            for name, tokens in sorted(pack.stats.tokens_per_section.items()):
                print(f"  {name}: {tokens}")

    raise typer.Exit(0 if ok else 1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for the ``contextweaver`` CLI."""
    try:
        app()
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

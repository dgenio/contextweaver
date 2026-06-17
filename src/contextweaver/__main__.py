"""Command-line interface for contextweaver.

Provides sub-commands / sub-apps:

demo        Run a built-in demonstration of both engines.
build       Build a routing graph from a catalog JSON file.
route       Route a query over a pre-built routing graph.
print-tree  Pretty-print the routing tree for a graph.
init        Scaffold contextweaver config + sample catalog in cwd.
ingest      Ingest a JSONL session into a serialised session file.
replay      Replay a session and build context for a given phase.
stats       Render a human-readable :class:`BuildStats` diagnostic report
            from an ingested session (issue #106).
inspect     Render a payload-safe context/routing/artifact report (issue #398).
budget-check
            Assert an ingested session's rendered prompt stays under a
            token ceiling for CI regression checks (issue #276).
verify      Verify library installation and core functionality
            without network dependencies (issue #657).
mcp serve   [experimental] Run contextweaver as a stdio MCP server
            (gateway or proxy mode) in front of an upstream catalog
            (issues #243, #246).

Invocable as ``python -m contextweaver`` or ``contextweaver`` (via
``[project.scripts]``).  Exempt from the 300-line module limit.

Built on `Typer <https://typer.tiangolo.com>`_ + `Rich <https://rich.readthedocs.io>`_
(both core dependencies as of v0.5; the legacy ``[cli]`` extra is kept as an
empty alias for one cycle).  Issue #221.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table
from rich.tree import Tree as RichTree

from contextweaver._mcp_cli import mcp_app
from contextweaver._verify import (
    _check_build,
    _check_import,
    _check_manager,
    _check_routing,
    _check_tokens,
    _VerifyCheck,
)
from contextweaver.adapters._sidecar_http import serve_api
from contextweaver.adapters.gateway_policy import RateLimit
from contextweaver.adapters.mcp import mcp_tool_to_selectable
from contextweaver.adapters.sidecar import SidecarApp, SidecarConfig
from contextweaver.config import ContextBudget
from contextweaver.context.consolidation import consolidate
from contextweaver.context.consolidation_types import ConsolidationPolicy
from contextweaver.context.manager import ContextManager
from contextweaver.eval.dataset import EvalDataset
from contextweaver.eval.routing import evaluate_routing
from contextweaver.exceptions import CatalogError, ContextWeaverError
from contextweaver.inspection import build_inspection_report, render_inspection_report
from contextweaver.routing.cards import make_choice_cards, render_cards_text
from contextweaver.routing.catalog import (
    CatalogValidationReport,
    generate_sample_catalog,
    load_catalog,
    load_catalog_dicts,
    load_catalog_json,
    validate_references,
)
from contextweaver.routing.graph_io import load_graph, save_graph
from contextweaver.routing.normalizer import CatalogNormalizer, NormalizationReport
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.store.episodic import InMemoryEpisodicStore
from contextweaver.store.facts import InMemoryFactStore
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

# ``catalog`` groups authoring-time catalog tooling (issue #538).  Kept as a
# sub-app so future catalog commands (e.g. ``catalog diff``, issue #514) share
# the namespace without crowding the top-level help.
catalog_app = typer.Typer(
    name="catalog",
    help="Author-time tooling for tool catalogs (lint, ...).",
    no_args_is_help=True,
    add_completion=False,
)


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


class _InspectFormatChoice(str, Enum):
    json = "json"
    markdown = "markdown"


class _DemoScenario(str, Enum):
    default = "default"
    large_catalog = "large-catalog"
    huge_tool_output = "huge-tool-output"
    mcp_gateway = "mcp-gateway"
    mcp_gateway_full = "mcp-gateway-full"
    killer = "killer"


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
def demo(
    scenario: Annotated[
        _DemoScenario,
        typer.Option(
            "--scenario",
            help=(
                "Which scenario to run. "
                "default = friendly walkthrough on a small event log; "
                "large-catalog = 1,000 tools shortlisted to compact ChoiceCards; "
                "huge-tool-output = context firewall on a ~10 KB tool result; "
                "mcp-gateway = MCP gateway meta-tools end-to-end (3 stub tools, no network); "
                "mcp-gateway-full = full 60-tool MCP Context Gateway architecture (issue #264); "
                "killer = the 60-second failure mode: 100 tools + huge output, naive vs "
                "contextweaver (issue #322)."
            ),
        ),
    ] = _DemoScenario.default,
) -> None:
    """Run a built-in demonstration scenario."""
    from contextweaver import _demos

    dispatch: dict[_DemoScenario, Any] = {
        _DemoScenario.default: _demos.run_default,
        _DemoScenario.large_catalog: _demos.run_large_catalog,
        _DemoScenario.huge_tool_output: _demos.run_huge_tool_output,
        _DemoScenario.mcp_gateway: _demos.run_mcp_gateway,
        _DemoScenario.mcp_gateway_full: _demos.run_mcp_gateway_full,
        _DemoScenario.killer: _demos.run_killer,
    }
    dispatch[scenario]()


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


@app.command("inspect")
def inspect_cmd(
    session: Annotated[Path, typer.Option(..., help="Path to the session JSON file.")],
    phase: Annotated[
        _PhaseChoice, typer.Option(help="Context phase to inspect.")
    ] = _PhaseChoice.answer,
    budget: Annotated[int, typer.Option(help="Token budget for the context build.")] = 4000,
    format: Annotated[  # noqa: A002
        _InspectFormatChoice, typer.Option("--format", help="Output format.")
    ] = _InspectFormatChoice.markdown,
    graph: Annotated[Path | None, typer.Option(help="Optional routing graph to inspect.")] = None,
    catalog: Annotated[Path | None, typer.Option(help="Catalog paired with --graph.")] = None,
    route_query: Annotated[
        str | None, typer.Option("--route-query", help="Query used with --graph and --catalog.")
    ] = None,
) -> None:
    """Inspect context decisions, optional routing, and artifact metadata."""
    routing_requested = any(value is not None for value in (graph, catalog, route_query))
    if routing_requested and (graph is None or catalog is None or route_query is None):
        raise typer.BadParameter(
            "--graph, --catalog, and --route-query must be supplied together",
            param_hint="--graph",
        )

    manager = _restore_manager_from_session(str(session), budget)
    pack, explanation = manager.build_sync(
        phase=Phase(phase.value),
        query="inspect",
        budget_tokens=budget,
        explain=True,
    )
    raw_session: dict[str, Any] = json.loads(session.read_text(encoding="utf-8"))
    artifacts: list[dict[str, Any]] = []
    for handle, metadata in raw_session.get("artifacts", {}).items():
        item = dict(metadata) if isinstance(metadata, dict) else {}
        item["handle"] = handle
        artifacts.append(item)

    routing: dict[str, Any] | None = None
    if graph is not None and catalog is not None and route_query is not None:
        graph_obj = load_graph(str(graph))
        items = load_catalog(str(catalog))
        graph_ids = set(graph_obj.items())
        router = Router(
            graph_obj,
            items=[item for item in items if item.id in graph_ids],
        )
        routing = router.route(route_query).to_dict(include_items=False)

    report = build_inspection_report(
        pack,
        explanation=explanation,
        artifacts=artifacts,
        routing=routing,
        budget=budget,
    )
    if format == _InspectFormatChoice.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_inspection_report(report), end="")


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


@app.command("eval")
def eval_cmd(
    dataset: Annotated[
        Path, typer.Option(..., help="Path to the gold-standard dataset JSON file.")
    ],
    catalog: Annotated[Path, typer.Option(..., help="Path to the tool catalog JSON/YAML file.")],
    top_k: Annotated[int, typer.Option("--top-k", help="Max routing results per query.")] = 10,
    beam_width: Annotated[int, typer.Option("--beam-width", help="Beam width.")] = 3,
    max_children: Annotated[
        int, typer.Option("--max-children", help="Max children per node when building the graph.")
    ] = 10,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Evaluate routing quality against a gold-standard dataset (issue #12)."""
    items = load_catalog(str(catalog))
    graph = TreeBuilder(max_children=max_children).build(items)
    router = Router(graph, items=items, beam_width=beam_width, top_k=top_k)
    ds = EvalDataset.load(dataset)
    report = evaluate_routing(router, ds, catalog_ids={it.id for it in items})

    if json_output:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(report.summary())


# ---------------------------------------------------------------------------
# consolidate (issue #498)
# ---------------------------------------------------------------------------


@app.command("consolidate")
def consolidate_cmd(
    episodes: Annotated[
        Path,
        typer.Option(..., "--episodes", help="Episodic-store JSON file ({'episodes': [...]})."),
    ],
    facts: Annotated[
        Path | None,
        typer.Option("--facts", help="Optional fact-store JSON file ({'facts': [...]})."),
    ] = None,
    apply: Annotated[
        bool, typer.Option("--apply", help="Write promoted facts into the fact store.")
    ] = False,
    facts_out: Annotated[
        Path | None,
        typer.Option("--facts-out", help="Write the updated fact store here (with --apply)."),
    ] = None,
    min_occurrences: Annotated[
        int, typer.Option("--min-occurrences", help="Min clustered episodes to promote.")
    ] = 3,
    min_sessions: Annotated[
        int, typer.Option("--min-sessions", help="Min distinct sessions to promote.")
    ] = 2,
    similarity: Annotated[
        float, typer.Option("--similarity", help="Jaccard similarity threshold for clustering.")
    ] = 0.5,
    decay_after_days: Annotated[
        int,
        typer.Option("--decay-after-days", help="Decay horizon in days; negative disables decay."),
    ] = 90,
    as_of: Annotated[
        str | None,
        typer.Option("--as-of", help="ISO-8601 reference time for decay reporting."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Consolidate episodic memory into durable facts (issue #498).

    Loads an episodic store (and an optional fact store) from JSON, runs the
    deterministic consolidation pipeline, and prints the report. With
    ``--apply`` the promoted facts are upserted into the fact store; pass
    ``--facts-out`` to persist the updated store.
    """
    try:
        ep_store = InMemoryEpisodicStore.from_dict(json.loads(episodes.read_text(encoding="utf-8")))
        fact_store = (
            InMemoryFactStore.from_dict(json.loads(facts.read_text(encoding="utf-8")))
            if facts is not None
            else InMemoryFactStore()
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(f"could not read store JSON: {exc}") from exc

    policy = ConsolidationPolicy(
        min_occurrences=min_occurrences,
        min_sessions=min_sessions,
        similarity_threshold=similarity,
        decay_after_days=None if decay_after_days < 0 else decay_after_days,
    )
    parsed_as_of = None
    if as_of is not None:
        try:
            parsed_as_of = datetime.fromisoformat(as_of)
        except ValueError as exc:
            raise typer.BadParameter(f"invalid --as-of timestamp: {exc}") from exc

    try:
        report = consolidate(ep_store, fact_store, policy, as_of=parsed_as_of, apply=apply)
    except ContextWeaverError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if apply and facts_out is not None:
        facts_out.write_text(
            json.dumps(fact_store.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
        )

    if json_output:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(report.summary())


# ---------------------------------------------------------------------------
# verify (issue #657)
# ---------------------------------------------------------------------------


@app.command()
def verify(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON for CI/automation."),
    ] = False,
) -> None:
    """Verify library installation and core functionality (issue #657).

    Runs deterministic, network-free checks covering:
    import path, ContextManager instantiation, a minimal context build,
    token counting, and routing.  Prints pass/fail output with actionable
    next steps.  Exit code 0 when all checks pass, 1 otherwise.
    """
    checks: list[_VerifyCheck] = [
        _check_import(),
        _check_manager(),
        _check_build(),
        _check_tokens(),
        _check_routing(),
    ]
    all_ok = all(c.ok for c in checks)
    next_step = (
        "Try `contextweaver demo` for a guided walkthrough, or "
        "visit https://dgenio.github.io/contextweaver/quickstart/ "
        "for a 10-minute tutorial."
    )

    if json_output:
        payload = {
            "ok": all_ok,
            "checks": [
                {
                    "name": c.name,
                    "ok": c.ok,
                    "detail": c.detail,
                    "fix_hint": c.fix_hint,
                }
                for c in checks
            ],
            "next_step": next_step,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        table = Table(
            title="[bold]contextweaver verify[/bold]",
            show_header=True,
            header_style="bold",
        )
        table.add_column("Check")
        table.add_column("Status")
        table.add_column("Detail")
        for c in checks:
            status = "[green]PASS[/green]" if c.ok else "[red]FAIL[/red]"
            table.add_row(c.name, status, c.detail)
        _console.print(table)
        if all_ok:
            _console.print("[bold green]All checks passed.[/bold green]")
        else:
            for c in checks:
                if not c.ok and c.fix_hint:
                    _console.print(f"[red]- {c.name}:[/red] {c.fix_hint}")
        _console.print(f"\n[dim]Next step:[/dim] {next_step}")

    raise typer.Exit(0 if all_ok else 1)


# ---------------------------------------------------------------------------
# catalog lint (issue #538)
# ---------------------------------------------------------------------------

#: CLI exit codes for ``catalog lint`` (documented in docs/tool_router.md).
_LINT_EXIT_CLEAN = 0
_LINT_EXIT_FINDINGS = 1
_LINT_EXIT_LOAD_ERROR = 3


def _load_lint_items(path: Path) -> list[SelectableItem]:
    """Load catalog items for linting from any accepted shape (issue #538).

    Accepts the contextweaver-native JSON/YAML catalog, a raw MCP
    ``tools/list`` array, and the ``{"tools": [...]}`` snapshot wrapper used
    by the gateway recipes.  References are *not* validated here (the lint
    command reports them separately), so loading uses ``on_invalid="ignore"``.

    Raises:
        CatalogError: If the file cannot be parsed or no tool entries are found.
    """
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")  # OSError handled by the caller
    if suffix in (".yaml", ".yml"):
        import yaml  # core dep — see pyproject.toml

        try:
            raw: Any = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise CatalogError(f"invalid YAML: {exc}") from exc
    elif suffix == ".json":
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CatalogError(f"invalid JSON: {exc}") from exc
    else:
        raise CatalogError(f"unsupported catalog format {suffix!r}; use .json, .yaml, or .yml")

    if isinstance(raw, dict) and isinstance(raw.get("tools"), list):
        raw = raw["tools"]
    if not isinstance(raw, list) or not raw:
        raise CatalogError(
            "catalog must be a non-empty sequence of tool entries "
            "(or a snapshot object with a non-empty 'tools' list)"
        )

    native_keys = {"id", "kind", "name", "description"}
    is_native = all(isinstance(e, dict) and native_keys.issubset(e) for e in raw)
    if is_native:
        return load_catalog_dicts(raw, on_invalid="ignore")
    # Otherwise treat each entry as an MCP tools/list definition.
    return [mcp_tool_to_selectable(entry) for entry in raw]


def _lint_payload(norm: NormalizationReport, refs: CatalogValidationReport) -> dict[str, Any]:
    """Assemble the machine-readable ``catalog lint --json`` payload."""
    ok = norm.changed_count == 0 and not norm.invalid_ids and refs.ok
    return {
        "ok": ok,
        "normalization": norm.to_dict(),
        "references": refs.to_dict(),
    }


@catalog_app.command("lint")
def catalog_lint(
    file: Annotated[Path, typer.Argument(help="Path to the catalog JSON/YAML file.")],
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Lint a tool catalog for metadata hygiene and broken references (issue #538).

    Surfaces the existing :class:`CatalogNormalizer` findings (missing
    descriptions, duplicate/blank IDs, tag and whitespace issues) plus
    cross-item reference findings (``depends_on`` / ``requires``).  Exits
    ``0`` when clean, ``1`` when findings are present, and ``3`` on a load
    error — suitable as a pre-deploy CI gate.  Input files are never modified.
    """
    try:
        items = _load_lint_items(file)
    except (CatalogError, OSError, json.JSONDecodeError) as exc:
        print(f"Error: cannot lint {file}: {exc}", file=sys.stderr)
        raise typer.Exit(_LINT_EXIT_LOAD_ERROR) from exc

    norm = CatalogNormalizer().normalize(items)[1]
    refs = validate_references(items)
    payload = _lint_payload(norm, refs)

    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _render_lint_report(file, norm, refs, ok=bool(payload["ok"]))

    raise typer.Exit(_LINT_EXIT_CLEAN if payload["ok"] else _LINT_EXIT_FINDINGS)


def _render_lint_report(
    file: Path,
    norm: NormalizationReport,
    refs: CatalogValidationReport,
    *,
    ok: bool,
) -> None:
    """Print a human-readable ``catalog lint`` report (Rich)."""
    _console.print(f"[bold]Catalog lint:[/bold] {file} ({norm.items_processed} items)")
    if ok:
        _console.print("[bold green]OK[/bold green] no findings")
        return

    findings: list[tuple[str, str, str]] = []
    for item_id in norm.invalid_ids:
        findings.append(("error", "blank/duplicate id", repr(item_id)))
    if norm.description_filled_count:
        findings.append(
            ("warning", "missing description", f"{norm.description_filled_count} item(s)")
        )
    if norm.tag_dedup_count:
        findings.append(("warning", "duplicate tags", f"{norm.tag_dedup_count} item(s)"))
    if norm.whitespace_normalized_count:
        findings.append(("warning", "whitespace", f"{norm.whitespace_normalized_count} item(s)"))
    for finding in refs.findings:
        findings.append(("error", f"broken {finding.field}", finding.message()))

    table = Table(title=None, show_header=True, header_style="bold")
    table.add_column("Severity")
    table.add_column("Finding")
    table.add_column("Detail")
    for severity, kind, detail in findings:
        colour = "red" if severity == "error" else "yellow"
        table.add_row(f"[{colour}]{severity}[/{colour}]", kind, detail)
    _console.print(table)
    _console.print(f"[bold red]FAIL[/bold red] {len(findings)} finding(s)")


@app.command("serve-api")
def serve_api_command(
    catalog: Annotated[
        Path | None,
        typer.Option(help="Tool catalog JSON file. Omit to serve /v1/compact only."),
    ] = None,
    host: Annotated[str, typer.Option(help="Interface to bind.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="TCP port to listen on.")] = 8731,
    top_k: Annotated[
        int, typer.Option("--top-k", help="Routing ceiling: max candidates per /v1/route call.")
    ] = 50,
    beam_width: Annotated[int, typer.Option("--beam-width", help="Beam width.")] = 3,
    api_key: Annotated[
        str | None,
        typer.Option(
            help="Require this bearer token on /v1/route and /v1/compact.",
            envvar="CONTEXTWEAVER_SIDECAR_API_KEY",
        ),
    ] = None,
    rate_per_minute: Annotated[
        int | None, typer.Option(help="Per-client sliding-window request cap per minute.")
    ] = None,
    max_body_bytes: Annotated[
        int, typer.Option(help="Reject request bodies larger than this many bytes.")
    ] = 1_048_576,
) -> None:
    """Serve the language-agnostic HTTP sidecar (route/compact) — issue #427.

    Exposes ``POST /v1/route`` (tool routing) and ``POST /v1/compact``
    (tool-result compaction) over HTTP/JSON so non-Python agents can use the
    deterministic router and context firewall without embedding Python.
    ``GET /v1/health`` is an unauthenticated liveness probe.
    """
    router: Router | None = None
    if catalog is not None:
        items = load_catalog_json(str(catalog))
        graph = TreeBuilder().build(items)
        router = Router(graph, items=items, beam_width=beam_width, top_k=top_k)
        print(f"Loaded {len(items)} catalog items from {catalog}; /v1/route enabled")
    else:
        print("No catalog provided; serving /v1/compact only (/v1/route disabled)")

    rate_limit = RateLimit(max_calls_per_minute=rate_per_minute) if rate_per_minute else None
    config = SidecarConfig(api_key=api_key, rate_limit=rate_limit, max_body_bytes=max_body_bytes)
    app_obj = SidecarApp(router=router, config=config)
    print(f"contextweaver sidecar listening on http://{host}:{port} (Ctrl-C to stop)")
    serve_api(app_obj, host=host, port=port)


# ---------------------------------------------------------------------------
# Sub-apps
# ---------------------------------------------------------------------------

# ``mcp serve`` lives in its own module to keep this file lean (issue #243/#246).
app.add_typer(mcp_app)
app.add_typer(catalog_app, name="catalog")


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
    except (ContextWeaverError, ValueError, PermissionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

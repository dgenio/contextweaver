"""MCP Context Gateway — real-world catalog variant (issue #280).

Sibling of :mod:`main`. Same architecture (Router → ChoiceCards →
hydrate → firewall → answer-phase build), but the tool catalog is loaded
from snapshots of *real public MCP servers* committed under
``real_catalogs/`` instead of the hand-crafted 60-tool ``catalog.yaml``.

Why this exists: the offline demo answers the question "how does this
look against a hand-tuned 60-tool catalog?" — but the natural follow-up
is "how does it perform against a real MCP server's tool list?". This
variant loads three real catalogs (time, filesystem, everything) and
walks the same shape end-to-end so users can see the engine running
against catalogs that weren't designed for the demo.

For each snapshot the script reports:

- Catalog size + namespace cardinality.
- A routing query targeted at one of the real tools.
- The bounded ChoiceCard shortlist (top-5).
- Hydration of the selected tool's actual ``inputSchema``.
- The post-firewall answer-phase prompt token count.

Run standalone::

    python examples/architectures/mcp_context_gateway/main_real.py

Or via ``make architectures`` / ``make example``.

The snapshots ship under ``real_catalogs/*.json``. Regenerate them with
``python scripts/capture_mcp_catalog.py`` (offline-safe — keeps existing
snapshots if the upstream server is unreachable).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from contextweaver.adapters.mcp import mcp_tool_to_selectable
from contextweaver.config import ContextBudget
from contextweaver.context.manager import ContextManager
from contextweaver.exceptions import CatalogError
from contextweaver.routing.cards import make_choice_cards, render_cards_text
from contextweaver.routing.catalog import Catalog
from contextweaver.routing.hydration import SchemaSource, hydrate_with_schema
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import ContextItem, ItemKind, Phase

REAL_CATALOGS_DIR = Path(__file__).parent / "real_catalogs"

# (snapshot file, routing query, intent tool name, canned upstream result).
SCENARIOS: list[tuple[str, str, str, str]] = [
    (
        "time.json",
        "What time is it right now in Tokyo?",
        "get_current_time",
        "2026-05-20T16:42:00+09:00 (Asia/Tokyo)",
    ),
    (
        "filesystem.json",
        "List the files in /tmp/project so I can find the changelog",
        "list_directory",
        "[DIR] src/\n[DIR] tests/\n[FILE] CHANGELOG.md\n[FILE] README.md\n[FILE] pyproject.toml",
    ),
    (
        "everything.json",
        "Add 17 and 25 for me",
        "add",
        "42",
    ),
]


def _print_header(title: str) -> None:
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def _load_snapshot(path: Path) -> list[dict[str, Any]]:
    """Read a real-catalog snapshot and return the list of tool defs."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or "tools" not in payload:
        raise SystemExit(f"{path} is not a valid catalog snapshot — expected {{'tools': [...]}}")
    return list(payload["tools"])


def _build_catalog_from_mcp_tools(tool_defs: list[dict[str, Any]]) -> Catalog:
    """Convert MCP tool defs to a contextweaver Catalog.

    Uses the existing ``adapters.mcp.mcp_tool_to_selectable`` so the
    real-catalog path reuses the production wire-shape converter rather
    than duplicating it.
    """
    catalog = Catalog()
    for tool_def in tool_defs:
        item = mcp_tool_to_selectable(tool_def)
        # Skip the rare case where the same tool name appears twice in
        # a snapshot — the upstream wire contract forbids duplicates,
        # but defensive code makes the example robust to snapshot drift.
        # Narrow the catch to CatalogError so adapter regressions or other
        # unexpected bugs surface loudly instead of silently dropping tools.
        try:
            catalog.register(item)
        except CatalogError:
            continue
    return catalog


def _scenario(snapshot_name: str, routing_query: str, intent: str, canned: str) -> None:
    """Run a single real-catalog scenario."""
    _print_header(f"{snapshot_name} — {routing_query!r}")
    path = REAL_CATALOGS_DIR / snapshot_name
    tool_defs = _load_snapshot(path)
    catalog = _build_catalog_from_mcp_tools(tool_defs)
    items = catalog.all()
    namespaces = sorted({item.namespace for item in items})
    print(f"loaded:    {snapshot_name}  ({len(items)} tools, {len(namespaces)} namespaces)")
    print(f"namespaces: {namespaces}")

    # The snapshot itself doubles as the schema source: every entry has
    # inputSchema inline, exactly the shape ``SchemaSource.from_mcp_tools``
    # consumes. No second sidecar file needed for real catalogs.
    schemas = SchemaSource.from_mcp_tools(tool_defs)

    graph = TreeBuilder(max_children=8).build(items)
    router = Router(graph, items=items, top_k=5)

    budget = ContextBudget(route=1500, call=2000, interpret=3000, answer=4000)
    mgr = ContextManager(budget=budget)

    u_id = f"u_{snapshot_name}"
    mgr.ingest_sync(ContextItem(id=u_id, kind=ItemKind.user_turn, text=routing_query))

    # Route.
    route = router.route(routing_query)
    shortlist = route.candidate_ids
    cards = make_choice_cards(
        route.candidate_items,
        scores=dict(zip(shortlist, route.scores, strict=False)),
    )
    # The catalog produced by `mcp_tool_to_selectable` uses the canonical
    # ``namespace:name#hash8`` id format — pick by matching the *name*
    # segment of the canonical id rather than relying on raw equality.
    chosen = next(
        (cid for cid in shortlist if cid.split(":", 1)[-1].split("#", 1)[0] == intent),
        shortlist[0],
    )
    print(f"shortlist: {shortlist}")
    print(f"chosen:    {chosen}  (intent={intent!r})")
    print(f"\nChoiceCards rendered to the model ({len(render_cards_text(cards))} chars):")
    print(render_cards_text(cards))

    # Hydrate using the real snapshot's inputSchema.
    hydrated = hydrate_with_schema(catalog, chosen, schemas)
    schema_chars = len(json.dumps(hydrated.args_schema))
    print(f"hydrated schema for {chosen!r}: {schema_chars} chars")
    if hydrated.args_schema:
        print(f"  required: {hydrated.args_schema.get('required', [])}")

    # Tool call + firewall (most real-MCP responses are small, so the
    # firewall typically no-ops here — that's the point: it doesn't tax
    # short responses).
    tc_id = f"tc_{snapshot_name}"
    mgr.ingest_sync(
        ContextItem(id=tc_id, kind=ItemKind.tool_call, text=f"{chosen}(...)", parent_id=u_id)
    )
    mcp_result = {"content": [{"type": "text", "text": canned}], "isError": False}
    item, envelope = mgr.ingest_mcp_result(
        tool_call_id=tc_id,
        mcp_result=mcp_result,
        tool_name=chosen,
        firewall_threshold=2000,
    )
    raw_chars = len(canned)
    summary_chars = len(item.text)
    print(
        f"firewall:  {raw_chars} chars -> {summary_chars} chars  "
        f"(no-op={raw_chars == summary_chars})"
    )

    # Answer phase.
    answer = mgr.build_sync(phase=Phase.answer, query=routing_query)
    print(
        f"answer:    tokens={answer.stats.prompt_tokens}  "
        f"chars={len(answer.prompt):,}  included={answer.stats.included_count}"
    )


def main() -> None:
    """Run the architecture against every committed real-catalog snapshot."""
    _print_header("contextweaver -- MCP Context Gateway (REAL catalogs)")
    print(
        "(running the same routing + firewall + answer-phase shape against "
        "snapshots of real public MCP servers)"
    )

    for snapshot, query, intent, canned in SCENARIOS:
        _scenario(snapshot, query, intent, canned)

    _print_header("Real-catalog scenarios complete")
    print(
        "All three real-catalog scenarios walked the same route -> call -> "
        "interpret -> answer cycle that the offline 60-tool architecture uses. "
        "No catalog modifications were needed — the existing routing engine, "
        "schema-hydration helper (#261), and context firewall handle the real "
        "wire shape verbatim."
    )


if __name__ == "__main__":
    main()

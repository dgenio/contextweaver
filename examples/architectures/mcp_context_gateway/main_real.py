"""MCP Context Gateway — real-catalog variant (issue #280).

Companion to :mod:`main`. Both walk the same route -> call -> interpret ->
answer cycle, but where :mod:`main` runs against a hand-curated 60-tool
``catalog.yaml``, this script runs against the verbatim ``tools/list``
payloads of three real, MIT-licensed MCP reference servers committed under
:mod:`real_catalogs`:

- ``@modelcontextprotocol/server-filesystem`` — 11 filesystem tools.
- ``mcp-server-git`` — 12 git tools.
- ``mcp-server-fetch`` — 1 fetch tool.

The script answers the natural follow-up to the mocked architecture:
*"How does the gateway pattern look on a real MCP server?"*. For each
snapshot we:

1. Load the snapshot through :func:`mcp_tool_to_selectable` — same code
   path that ``ProxyRuntime.register_tool_defs_sync`` uses.
2. Route a representative natural-language query against the catalog and
   render the top-5 ``ChoiceCards``.
3. Hydrate ONLY the chosen tool's full schema (lazy hydration).
4. Ingest a plausibly large fake upstream response through the context
   firewall.
5. Print a metrics block byte-stable across runs.

The run is deterministic: snapshots are committed, queries are constant,
no network or real MCP server is contacted at run time.

Run standalone::

    python examples/architectures/mcp_context_gateway/main_real.py

Or via ``make architectures``. Re-snapshot upstream catalogs with
``scripts/snapshot_mcp_catalog.py`` when servers ship new versions.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Allow `from scenarios import ...` whether this module is launched directly
# (`python examples/.../main_real.py`) or loaded via `importlib.util` from a
# test fixture. Direct invocation already puts `__file__`'s directory on
# sys.path; the explicit insert covers the importlib path.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from scenarios import _SCENARIOS, _Scenario  # noqa: E402,F401

from contextweaver.adapters.mcp import mcp_tool_to_selectable  # noqa: E402
from contextweaver.config import ContextBudget  # noqa: E402
from contextweaver.context.manager import ContextManager  # noqa: E402
from contextweaver.routing.cards import make_choice_cards, render_cards_text  # noqa: E402
from contextweaver.routing.catalog import Catalog  # noqa: E402
from contextweaver.routing.router import Router  # noqa: E402
from contextweaver.routing.tree import TreeBuilder  # noqa: E402
from contextweaver.types import ContextItem, ItemKind, Phase  # noqa: E402

REAL_CATALOGS_DIR = _HERE / "real_catalogs"


def _print_header(title: str) -> None:
    """Print a section banner consistent with ``main.py``."""
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def _load_snapshot(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return ``(_meta, tools)`` from a real-catalog snapshot file."""
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict) or "tools" not in payload:
        raise SystemExit(
            f"snapshot {path.name} is malformed: expected JSON object with a 'tools' key"
        )
    meta = payload.get("_meta") or {}
    tools = payload["tools"]
    if not isinstance(tools, list):
        raise SystemExit(f"snapshot {path.name} 'tools' field is not a list")
    return meta, tools


def _build_router(catalog: Catalog) -> Router:
    items = catalog.all()
    graph = TreeBuilder(max_children=10).build(items)
    return Router(graph, items=items, top_k=min(5, len(items)))


def _pick_intent_id(items: list[Any], intent_tool_name: str, shortlist: list[str]) -> str:
    """Match the upstream tool name to a canonical ``tool_id`` from the shortlist.

    The canonical ID is namespaced and hashed (see
    :func:`contextweaver.routing.tool_id.canonical_tool_id`), so we cannot
    compare strings directly. We look up the intent by ``SelectableItem.name``
    (which the adapter sets to the upstream name verbatim) and prefer the
    match if it is in the shortlist; otherwise fall back to ``shortlist[0]``.
    """
    by_name = {item.name: item.id for item in items}
    target_id = by_name.get(intent_tool_name)
    if target_id and target_id in shortlist:
        return target_id
    return shortlist[0]


def _run_scenario(scenario: _Scenario) -> dict[str, Any]:
    """Run one snapshot end-to-end and return its metrics block."""
    snapshot_path = REAL_CATALOGS_DIR / scenario.snapshot
    meta, tool_defs = _load_snapshot(snapshot_path)

    _print_header(f"contextweaver -- real MCP catalog: {scenario.title}")
    source = meta.get("source") or "<unknown source>"
    version = meta.get("server_version") or "<unknown version>"
    licence = meta.get("license") or "<unknown licence>"
    print(f"snapshot: {scenario.snapshot}")
    print(f"upstream: {source} {version} ({licence})")

    # ------------------------------------------------------------------
    # 1. Load catalog via the real MCP adapter.
    # ------------------------------------------------------------------
    catalog = Catalog()
    for tool_def in tool_defs:
        catalog.register(mcp_tool_to_selectable(tool_def))
    items = catalog.all()
    catalog_tools = len(items)
    ns_count = len({it.namespace for it in items})
    print(f"\nLoaded catalog: {catalog_tools} tools across {ns_count} namespaces")

    router = _build_router(catalog)
    budget = ContextBudget(route=1500, call=2000, interpret=3000, answer=4000)
    mgr = ContextManager(budget=budget)

    # ------------------------------------------------------------------
    # 2. Route phase — produce a top-k shortlist of compact ChoiceCards.
    # ------------------------------------------------------------------
    _print_header(f"[1/5] Route — {scenario.title}")
    print(f"user typed:    {scenario.user_query!r}")
    print(f"routing query: {scenario.routing_query!r}")

    u_id = "u1"
    mgr.ingest_sync(ContextItem(id=u_id, kind=ItemKind.user_turn, text=scenario.user_query))

    result = router.route(scenario.routing_query)
    shortlist = result.candidate_ids
    chosen = _pick_intent_id(list(items), scenario.intent_tool_name, shortlist)
    chosen_item = next(it for it in items if it.id == chosen)
    cards = make_choice_cards(
        result.candidate_items,
        scores=dict(zip(shortlist, result.scores, strict=False)),
    )
    rendered = render_cards_text(cards)
    print(f"shortlist ({len(shortlist)} of {catalog_tools}): {shortlist}")
    print(f"chosen:    {chosen}  (intent={scenario.intent_tool_name!r})")
    print(f"\nChoiceCards rendered to the model ({len(rendered)} chars, NO full schemas):")
    print(rendered)
    exposed_choice_cards = len(cards)

    # ------------------------------------------------------------------
    # 3. Call phase — hydrate ONLY the selected tool's schema.
    # ------------------------------------------------------------------
    _print_header(f"[2/5] Call — hydrate ONLY {scenario.intent_tool_name!r}")
    selected_schema = chosen_item.args_schema or {}
    schema_json = json.dumps(selected_schema, indent=2)
    skipped = max(catalog_tools - 1, 0)
    print(f"tool: {chosen}")
    print(f"hydrated schema for: {chosen!r}  ({len(schema_json)} chars)")
    print(f"hydrated schema for the other {skipped} tools: 0 chars (skipped)")
    print("\nSchema preview (first 200 chars):")
    print(f"  {schema_json[:200]!r}")
    if len(schema_json) > 200:
        print("  ...")

    # ------------------------------------------------------------------
    # 4. Tool call + 5. Firewall.
    # ------------------------------------------------------------------
    _print_header(f"[3/5] Tool call + [4/5] context firewall — {scenario.title}")
    tc_id = "tc1"
    chosen_args: dict[str, Any] = {}
    mgr.ingest_sync(
        ContextItem(
            id=tc_id,
            kind=ItemKind.tool_call,
            text=f"{chosen_item.name}({json.dumps(chosen_args, separators=(',', ':'))})",
            parent_id=u_id,
        )
    )
    print(f"called: {chosen_item.name}(...)")

    mcp_result = {
        "content": [{"type": "text", "text": scenario.fake_result_text}],
        "isError": False,
    }
    raw_text = mcp_result["content"][0]["text"]
    raw_result_chars = len(raw_text)
    print(f"raw upstream result: {raw_result_chars:,} chars (MCP wire shape)")

    item, envelope = mgr.ingest_mcp_result(
        tool_call_id=tc_id,
        mcp_result=mcp_result,
        tool_name=chosen_item.name,
        firewall_threshold=2000,
    )
    injected_summary_chars = len(item.text)
    artifact_handle = item.artifact_ref.handle if item.artifact_ref else "<none>"
    print(
        f"firewall: {raw_result_chars:,} chars  ->  {injected_summary_chars:,}-char "
        f"summary  (artifact {artifact_handle})"
    )

    # ------------------------------------------------------------------
    # 5. Answer phase.
    # ------------------------------------------------------------------
    _print_header(f"[5/5] Answer — {scenario.title}")
    answer = mgr.build_sync(phase=Phase.answer, query=scenario.routing_query)
    final_prompt = answer.prompt
    final_prompt_tokens = answer.stats.prompt_tokens
    print(f"answer prompt: included={answer.stats.included_count}  tokens={final_prompt_tokens}")
    print(f"final prompt length: {len(final_prompt):,} chars")

    saving_pct = 100.0 * (1.0 - injected_summary_chars / max(raw_result_chars, 1))

    metrics: dict[str, Any] = {
        "snapshot": scenario.snapshot,
        "catalog_tools": catalog_tools,
        "exposed_choice_cards": exposed_choice_cards,
        "hydrated_schema_chars": len(schema_json),
        "raw_result_chars": raw_result_chars,
        "injected_summary_chars": injected_summary_chars,
        "firewall_reduction_pct": round(saving_pct, 1),
        "artifact_handle": artifact_handle,
        "final_prompt_tokens": final_prompt_tokens,
        "final_prompt_chars": len(final_prompt),
    }

    _print_header(f"Metrics summary — {scenario.title}")
    for k, v in metrics.items():
        if isinstance(v, int) and k.endswith("_chars"):
            print(f"{k:24s}= {v:,}")
        elif k == "firewall_reduction_pct":
            print(f"{k:24s}= {v}%")
        else:
            print(f"{k:24s}= {v}")
    return metrics


def main() -> None:
    """Run every committed snapshot scenario end-to-end."""
    _print_header("contextweaver -- MCP Context Gateway (real-catalog variant)")
    print("(deterministic, network-free; snapshots committed under real_catalogs/)")

    all_metrics: list[dict[str, Any]] = []
    for scenario in _SCENARIOS:
        all_metrics.append(_run_scenario(scenario))

    _print_header("Aggregate metrics across all real snapshots")
    print(f"scenarios_run           = {len(all_metrics)}")
    total_raw = sum(m["raw_result_chars"] for m in all_metrics)
    total_summary = sum(m["injected_summary_chars"] for m in all_metrics)
    overall_pct = 100.0 * (1.0 - total_summary / max(total_raw, 1))
    print(f"total_raw_chars         = {total_raw:,}")
    print(f"total_summary_chars     = {total_summary:,}")
    print(f"overall_firewall_pct    = {overall_pct:.1f}%")


if __name__ == "__main__":
    main()

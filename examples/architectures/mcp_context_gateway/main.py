"""MCP Context Gateway — reference architecture.

A "DevOps Copilot" agent fronts a 60-tool catalog (analytics, bigquery,
billing, crm, docs, email, github, linear, pagerduty, slack). For a single
user question — *"Why did customer C-12345's MRR drop last month?"* — the
script walks the full route → call → interpret → answer cycle:

1. The :class:`Router` narrows the 60-tool catalog to a top-5 shortlist
   of :class:`ChoiceCard` s (compact tool descriptors — **no full schemas**).
2. The agent picks one tool from the shortlist via an explicit intent map.
   That separation is the load-bearing pattern: contextweaver bounds the
   choice; the agent (or in production, an LLM with the shortlist in its
   prompt) makes the final selection.
3. Only the selected tool's full JSON Schema is hydrated. The other 59
   never enter the prompt at any phase.
4. A mocked upstream returns a ~15 KB rowset, shaped as an MCP wire
   result (``{"content": [{"type": "text", "text": ...}], "isError": False}``).
5. :meth:`ContextManager.ingest_mcp_result` runs the result through the
   context firewall: the full bytes land in the artifact store, only a
   compact summary plus extracted facts enter the prompt.
6. The answer-phase build assembles the final budget-aware prompt — the
   raw rowset is **not** there; the summary, the artifact handle, and the
   dependency-linked tool call are.

This is **simulated**: tool implementations return canned strings, no real
MCP server / upstream gateway is touched. The simulation uses real
contextweaver primitives (``Router``, ``ContextManager``,
``ingest_mcp_result``) — same APIs you would wire to a live
``adapters.ProxyRuntime`` in production. See ``docs/integration_mcp.md``
and ``docs/gateway_spec.md`` for the live-gateway integration.

Run standalone::

    python examples/architectures/mcp_context_gateway/main.py

Or via ``make architectures`` / ``make example``.
"""

from __future__ import annotations

import json
from typing import Any

from contextweaver.config import ContextBudget
from contextweaver.context.manager import ContextManager
from contextweaver.data import gateway_catalog_path
from contextweaver.routing.cards import make_choice_cards, render_cards_text
from contextweaver.routing.catalog import Catalog, load_catalog_yaml
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import ContextItem, ItemKind, Phase

# Issue #264: the catalog ships inside ``contextweaver.data`` so this example
# (and the matching ``contextweaver demo --scenario mcp-gateway-full`` CLI
# scenario) work from a wheel install without needing the examples/ directory.
# ``gateway_catalog_path()`` returns a real filesystem ``Path`` regardless of
# whether the package is installed editable or extracted from a zip wheel.
CATALOG_PATH = gateway_catalog_path()

# The user-typed phrasing matters: a real agent would rephrase a vague
# natural-language question into a tool-shaped query before routing. We
# show that step explicitly so the demo is deterministic without hiding
# how a production system would arrive at this phrasing.
USER_TYPED_QUERY = "Why did customer C-12345's MRR drop last month?"
ROUTING_QUERY = "Execute a BigQuery query to find MRR delta rows for customer C-12345"
SELECTED_TOOL_ID = "bigquery.run_query"


def _mock_bigquery_result() -> dict[str, Any]:
    """Return a canned MCP-shaped tool result for the BigQuery call.

    The "result" is a JSON body of ~15 KB representing 90 daily MRR-change
    rows for customer ``C-12345`` over the last quarter. Shape mirrors what
    an MCP server typically returns: ``{"content": [{"type": "text",
    "text": ...}], "isError": False}``. Built lazily so importing this
    module from the test smoke harness does not pay the construction cost.
    """
    rows = []
    for day in range(1, 91):
        delta = -450 if day == 47 else (137 * day) % 600 - 300
        rows.append(
            {
                "date": f"2026-{(day - 1) // 30 + 2:02d}-{((day - 1) % 30) + 1:02d}",
                "customer_id": "C-12345",
                "plan": "growth" if day < 47 else "starter",
                "mrr_delta_usd": delta,
                "reason_code": "downgrade" if day == 47 else "noop",
                "actor": "self-serve" if day == 47 else "system",
                "notes": (
                    "self-serve downgrade via /billing/plan; "
                    "30-day notice satisfied; "
                    "retained 1 seat on Growth"
                    if day == 47
                    else f"daily reconcile, no plan change ({day})"
                ),
            }
        )
    body = "\n".join(json.dumps(r, sort_keys=True) for r in rows)
    body = (
        "rowset: bigquery.run_query\n"
        "project: ops-analytics-prod\n"
        f"rows_returned: {len(rows)}\n"
        "schema: date STRING, customer_id STRING, plan STRING, "
        "mrr_delta_usd INT64, reason_code STRING, actor STRING, notes STRING\n\n" + body + "\n"
    )
    return {"content": [{"type": "text", "text": body}], "isError": False}


def _print_header(title: str) -> None:
    """Print a section banner consistent with the other architecture script."""
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def _build_router(catalog: Catalog) -> Router:
    items = catalog.all()
    graph = TreeBuilder(max_children=10).build(items)
    return Router(graph, items=items, top_k=5)


def _select_from_shortlist(shortlist: list[str], intent: str) -> str:
    """Pick *intent* if it is in *shortlist*, else fall back to ``shortlist[0]``."""
    if intent in shortlist:
        return intent
    return shortlist[0]


def main() -> None:
    """Run the MCP Context Gateway scenario end-to-end."""
    _print_header("contextweaver -- MCP Context Gateway reference architecture")
    print("(simulated MCP gateway flow using contextweaver primitives)")

    # ------------------------------------------------------------------
    # 1. Load the 60-tool catalog and build a routing graph.
    # ------------------------------------------------------------------
    catalog = Catalog()
    for item in load_catalog_yaml(CATALOG_PATH):
        catalog.register(item)
    items = catalog.all()
    ns_count = len({it.namespace for it in items})
    catalog_tools = len(items)
    print(f"\nLoaded catalog: {catalog_tools} tools across {ns_count} namespaces")

    router = _build_router(catalog)
    budget = ContextBudget(route=1500, call=2000, interpret=3000, answer=4000)
    mgr = ContextManager(budget=budget)

    # ------------------------------------------------------------------
    # 2. Ingest the user turn and route to a bounded shortlist.
    # ------------------------------------------------------------------
    _print_header("[1/5] Route phase — model sees compact ChoiceCards, NOT schemas")
    print(f"user typed:    {USER_TYPED_QUERY!r}")
    print(f"routing query: {ROUTING_QUERY!r}")
    print("(a production agent would LLM-rephrase the user question into the routing query;")
    print(" here we hold both explicit so the demo is deterministic.)")

    u_id = "u1"
    mgr.ingest_sync(ContextItem(id=u_id, kind=ItemKind.user_turn, text=USER_TYPED_QUERY))

    result = router.route(ROUTING_QUERY)
    shortlist = result.candidate_ids
    chosen = _select_from_shortlist(shortlist, SELECTED_TOOL_ID)
    cards = make_choice_cards(
        result.candidate_items,
        scores=dict(zip(shortlist, result.scores, strict=False)),
    )
    rendered = render_cards_text(cards)
    print(f"shortlist ({len(shortlist)} of {catalog_tools}): {shortlist}")
    print(f"chosen:    {chosen}  (intent={SELECTED_TOOL_ID!r})")
    print(f"\nChoiceCards rendered to the model ({len(rendered)} chars, NO full schemas):")
    print(rendered)
    exposed_choice_cards = len(cards)

    # ------------------------------------------------------------------
    # 3. Call phase — hydrate ONLY the selected tool's schema.
    # ------------------------------------------------------------------
    _print_header("[2/5] Call phase — hydrate ONLY the selected tool's schema")
    # Issue #261: pull the schema from the catalog itself via Catalog.hydrate()
    # rather than a demo-special-case lookup table. The other 59 entries have
    # an empty args_schema in catalog.yaml, so this still proves the firewall
    # claim that only the selected tool's schema enters the prompt.
    hydrated = catalog.hydrate(chosen)
    selected_schema = hydrated.args_schema
    if not selected_schema:
        raise SystemExit(
            f"Catalog entry {chosen!r} has no args_schema; the demo catalog "
            "must carry a schema for the selected tool."
        )
    schema_json = json.dumps(selected_schema, indent=2, sort_keys=True)
    print(f"tool: {chosen}")
    print(f"hydrated schema for: {chosen!r}  ({len(schema_json)} chars)")
    print(f"hydrated schema for the other {catalog_tools - 1} tools: 0 chars (skipped)")
    print("\nSchema preview (first 200 chars):")
    print(f"  {schema_json[:200]!r}")
    if len(schema_json) > 200:
        print("  ...")

    # ------------------------------------------------------------------
    # 4. Tool call + 5. Firewall — large MCP result becomes summary + artifact.
    # ------------------------------------------------------------------
    _print_header("[3/5] Tool call + [4/5] context firewall")
    tc_id = "tc1"
    chosen_args = {
        "sql": (
            "SELECT date, plan, mrr_delta_usd, reason_code, actor, notes "
            "FROM `ops-analytics-prod.billing.mrr_changes` "
            "WHERE customer_id = 'C-12345' "
            "AND date BETWEEN '2026-02-01' AND '2026-04-30' "
            "ORDER BY date"
        ),
        "max_results": 1000,
    }
    mgr.ingest_sync(
        ContextItem(
            id=tc_id,
            kind=ItemKind.tool_call,
            text=f"{chosen}({json.dumps(chosen_args, separators=(',', ':'))})",
            parent_id=u_id,
        )
    )
    print(f"called: {chosen}(...)")

    mcp_result = _mock_bigquery_result()
    raw_text = mcp_result["content"][0]["text"]
    raw_result_chars = len(raw_text)
    print(f"raw upstream result: {raw_result_chars:,} chars (MCP wire shape)")

    item, envelope = mgr.ingest_mcp_result(
        tool_call_id=tc_id,
        mcp_result=mcp_result,
        tool_name=chosen,
        firewall_threshold=2000,
    )
    injected_summary_chars = len(item.text)
    artifact_handle = item.artifact_ref.handle if item.artifact_ref else "<none>"
    print(
        f"firewall: {raw_result_chars:,} chars  ->  {injected_summary_chars:,}-char "
        f"summary  (artifact {artifact_handle})"
    )
    if envelope.facts:
        print(f"extracted facts (first 3 of {len(envelope.facts)}):")
        for fact in envelope.facts[:3]:
            print(f"  - {fact}")

    # Persist a durable fact the answer can lean on without re-reading the rows.
    mgr.add_fact_sync(
        key="customer.C-12345.plan_change",
        value="growth -> starter (self-serve, day 47, -$450 MRR)",
        metadata={"source": chosen, "artifact": artifact_handle},
    )

    # ------------------------------------------------------------------
    # 5. Answer phase — final prompt: summary + handle + dependency chain.
    # ------------------------------------------------------------------
    _print_header("[5/5] Answer phase — final prompt sees summary + handle, NOT raw rows")
    answer = mgr.build_sync(phase=Phase.answer, query=ROUTING_QUERY)
    final_prompt = answer.prompt
    final_prompt_tokens = answer.stats.prompt_tokens
    print(f"answer prompt: included={answer.stats.included_count}  tokens={final_prompt_tokens}")
    print(f"final prompt length: {len(final_prompt):,} chars")
    # Sentinel must be unique to the deep rowset content — the firewall summary
    # reuses the leading header lines of the raw result, so a prefix-substring
    # check would false-positive. ``"mrr_delta_usd": -450`` appears only in
    # day 47's JSON row, deep in the rowset.
    rowset_sentinel = '"mrr_delta_usd": -450'
    leaked_raw = rowset_sentinel in final_prompt
    print(f"contains raw rowset? {'YES (regression!)' if leaked_raw else 'no'}")
    print(f"contains artifact handle? {'yes' if artifact_handle in final_prompt else 'no'}")
    print(f"contains durable fact?    {'yes' if 'plan_change' in final_prompt else 'no'}")
    print(f"contains user query?      {'yes' if USER_TYPED_QUERY in final_prompt else 'no'}")
    has_call = tc_id in final_prompt or chosen in final_prompt
    print(f"contains tool call?       {'yes' if has_call else 'no'}")
    print("\n--- Final answer-phase prompt ---")
    print(final_prompt)
    print("--- end prompt ---")

    # ------------------------------------------------------------------
    # 6. Summary metrics block.
    # ------------------------------------------------------------------
    _print_header("Metrics summary")
    print(f"catalog_tools           = {catalog_tools}")
    print(f"exposed_choice_cards    = {exposed_choice_cards}")
    print(f"hydrated_schema_chars   = {len(schema_json)}  (selected tool only)")
    print(f"raw_result_chars        = {raw_result_chars:,}")
    print(f"injected_summary_chars  = {injected_summary_chars:,}")
    saving_pct = 100.0 * (1.0 - injected_summary_chars / max(raw_result_chars, 1))
    print(f"firewall_reduction_pct  = {saving_pct:.1f}%")
    print(f"artifact_handle         = {artifact_handle}")
    print(f"final_prompt_tokens     = {final_prompt_tokens}")
    print(f"final_prompt_chars      = {len(final_prompt):,}")


if __name__ == "__main__":
    main()

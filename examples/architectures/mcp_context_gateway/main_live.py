"""MCP Context Gateway — live-transport variant (issue #260).

Sibling of :mod:`main` that walks the same five-step route → call → firewall
→ answer transcript but drives it through the real :class:`ProxyRuntime` +
:class:`StubUpstream` machinery instead of inlining the routing/firewall
calls. That makes this variant the closest in-process analogue to what
``contextweaver mcp serve --gateway --catalog ...`` will produce when an
external MCP client (Claude Desktop / Copilot / a bespoke agent) is on the
other end of the stdio pipe.

Why "live" without spawning a process: the goal is to exercise the real
``tool_browse`` / ``tool_execute`` / ``tool_view`` MCP wire shape (per
``docs/gateway_spec.md`` §4.2) and the firewall plumbing that
``ProxyRuntime.execute`` runs through. The only thing this variant skips
is the stdio transport layer itself — and the OUTPUT.md / test pin the
same metrics block as :mod:`main`, so any regression in the wire shape
shows up here.

Run standalone::

    python examples/architectures/mcp_context_gateway/main_live.py

Or via ``make architectures``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from contextweaver.adapters.mcp_gateway import (
    dispatch_meta_tool,
)
from contextweaver.adapters.mcp_upstream import StubUpstream
from contextweaver.adapters.proxy_runtime import ExposureMode, ProxyRuntime
from contextweaver.data import gateway_catalog_path
from contextweaver.routing.catalog import load_catalog_yaml

CATALOG_PATH = gateway_catalog_path()

USER_TYPED_QUERY = "Why did customer C-12345's MRR drop last month?"
ROUTING_QUERY = "Execute a BigQuery query to find MRR delta rows for customer C-12345"
SELECTED_TOOL_ID = "bigquery.run_query"


def _print_header(title: str) -> None:
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def _mock_bigquery_body() -> str:
    """Build the same 90-row rowset :mod:`main` uses, returned as plain text."""
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
    return (
        "rowset: bigquery.run_query\n"
        "project: ops-analytics-prod\n"
        f"rows_returned: {len(rows)}\n"
        "schema: date STRING, customer_id STRING, plan STRING, "
        "mrr_delta_usd INT64, reason_code STRING, actor STRING, notes STRING\n\n" + body + "\n"
    )


async def _bigquery_upstream(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Upstream handler for :class:`StubUpstream` — canned MRR rowset.

    Only ``bigquery.run_query`` returns the realistic rowset; other tools
    return a stub so the catalog stays exercisable but the firewall claim
    (98 %+ reduction) lands on the marquee path.
    """
    if name == "bigquery.run_query":
        return {
            "content": [{"type": "text", "text": _mock_bigquery_body()}],
            "isError": False,
        }
    return {
        "content": [
            {
                "type": "text",
                "text": f"stub: {name}({sorted(args.keys())})",
            }
        ],
        "isError": False,
    }


def _tool_defs_for_runtime() -> list[dict[str, Any]]:
    """Convert the packaged catalog into MCP-shaped tool defs for the upstream."""
    items = load_catalog_yaml(CATALOG_PATH)
    defs: list[dict[str, Any]] = []
    for item in items:
        defs.append(
            {
                "name": item.id,
                "description": item.description,
                "inputSchema": dict(item.args_schema) if item.args_schema else {"type": "object"},
            }
        )
    return defs


async def _run() -> None:
    _print_header("contextweaver -- MCP Context Gateway (live-transport variant)")
    print("(simulated MCP gateway flow via ProxyRuntime + StubUpstream)")

    tool_defs = _tool_defs_for_runtime()
    upstream = StubUpstream(tool_defs, handler=_bigquery_upstream)
    runtime = ProxyRuntime(
        upstream,
        mode=ExposureMode.GATEWAY,
        top_k=5,
        beam_width=3,
    )
    runtime.register_tool_defs_sync(tool_defs)
    catalog_tools = len(runtime.list_tool_ids())
    ns_count = len({tid.split(":", 1)[0].split(".", 1)[0] for tid in runtime.list_tool_ids()})
    print(f"\nLoaded catalog: {catalog_tools} tools, ~{ns_count} namespaces")

    # --- 1/5. Route phase — go through tool_browse (the live MCP wire) -----
    _print_header("[1/5] tool_browse(query=...)  ← MCP wire, NOT schemas")
    print(f"user typed:    {USER_TYPED_QUERY!r}")
    print(f"routing query: {ROUTING_QUERY!r}")
    browse = await dispatch_meta_tool(runtime, "tool_browse", {"query": ROUTING_QUERY, "top_k": 5})
    cards = json.loads(browse["content"][0]["text"])
    shortlist_ids = [card["id"] for card in cards]
    exposed_choice_cards = len(cards)
    chosen = SELECTED_TOOL_ID if SELECTED_TOOL_ID in shortlist_ids else shortlist_ids[0]
    print(f"shortlist ({exposed_choice_cards} of {catalog_tools}): {shortlist_ids}")
    print(f"chosen:    {chosen}  (intent={SELECTED_TOOL_ID!r})")
    rendered = json.dumps(cards, indent=2)
    print(f"\nChoiceCards as MCP-wire JSON ({len(rendered)} chars, NO full schemas):")
    print(rendered)

    # --- 2/5. Call phase — execute the meta-tool, which hydrates lazily ---
    _print_header("[2/5] tool_execute(tool_id, args)  ← schema hydrated under the hood")
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
    hydrated = runtime.hydrate(chosen)
    if hasattr(hydrated, "args_schema"):
        schema_json = json.dumps(dict(hydrated.args_schema), indent=2, sort_keys=True)  # type: ignore[union-attr]
    else:
        schema_json = "{}"
    print(f"tool: {chosen}")
    print(f"hydrated schema for: {chosen!r}  ({len(schema_json)} chars)")
    print(f"hydrated schema for the other {catalog_tools - 1} tools: 0 chars (skipped)")

    # --- 3/5 + 4/5. Tool call + firewall via dispatch_meta_tool -----------
    _print_header("[3/5] Tool call + [4/5] context firewall (real ProxyRuntime path)")
    exec_result = await dispatch_meta_tool(
        runtime,
        "tool_execute",
        {"tool_id": chosen, "args": chosen_args},
    )
    envelope_dict = json.loads(exec_result["content"][0]["text"])
    raw_text = _mock_bigquery_body()
    raw_result_chars = len(raw_text)
    injected_summary_chars = len(envelope_dict.get("summary", ""))
    # ProxyRuntime.execute persists oversized text through the firewall under
    # a deterministic ``text:<tool_id>:<sha>`` handle in the runtime's
    # artifact store; the envelope itself doesn't carry it. Read the store
    # directly so the metrics block stays comparable to ``main.py``.
    runtime_artifacts = runtime.context_manager.artifact_store.list_refs()
    artifact_handle = runtime_artifacts[0].handle if runtime_artifacts else "<none>"
    print(f"raw upstream result: {raw_result_chars:,} chars (mock BigQuery rowset)")
    print(
        f"firewall: {raw_result_chars:,} chars  ->  {injected_summary_chars}-char "
        f"summary  (artifact {artifact_handle})"
    )
    print(f"envelope status: {envelope_dict.get('status', 'unknown')}")
    if envelope_dict.get("facts"):
        facts = envelope_dict["facts"]
        print(f"extracted facts (first 3 of {len(facts)}):")
        for fact in facts[:3]:
            print(f"  - {fact}")

    # --- 5/5. tool_view through the dispatcher ----------------------------
    _print_header("[5/5] tool_view(handle, selector)  ← drilldown over real wire")
    if artifact_handle != "<none>":
        view = await dispatch_meta_tool(
            runtime,
            "tool_view",
            {"handle": artifact_handle, "selector": {"type": "head", "n_chars": 200}},
        )
        head_text = view["content"][0]["text"]
        print(f"tool_view returned {len(head_text)} chars:")
        print(head_text[:300])
    else:
        print("(no artifact handle persisted — text-only upstream response)")

    # --- Metrics block --------------------------------------------------------
    _print_header("Metrics summary (live)")
    saving = 100.0 * (1.0 - injected_summary_chars / max(raw_result_chars, 1))
    print(f"catalog_tools           = {catalog_tools}")
    print(f"exposed_choice_cards    = {exposed_choice_cards}")
    print(f"hydrated_schema_chars   = {len(schema_json)}  (selected tool only)")
    print(f"raw_result_chars        = {raw_result_chars:,}")
    print(f"injected_summary_chars  = {injected_summary_chars}")
    print(f"firewall_reduction_pct  = {saving:.1f}%")
    print(f"artifact_handle         = {artifact_handle}")


def main() -> None:
    """Run the live-transport MCP Context Gateway scenario end-to-end."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()

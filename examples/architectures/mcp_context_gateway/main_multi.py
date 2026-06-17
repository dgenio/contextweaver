"""MCP Context Gateway — multi-turn variant (issue #262).

Sibling of :mod:`main`. Same 60-tool catalog and same first turn (the
BigQuery investigation), but extends to a 4-turn transcript so the
architecture proves the gateway primitives compose under realistic
conversational pressure:

1. **Turn 1 — investigate MRR drop** (BigQuery query).
   Mirrors :mod:`main` so the comparison stays apples-to-apples.
2. **Turn 2 — who handled the downgrade?** (Linear ticket lookup).
   The bot uses the fact written in turn 1 (the plan-change summary)
   to constrain the lookup; the firewall summary still survives in
   the answer-phase prompt.
3. **Turn 3 — notify the account owner** (Slack post).
   Demonstrates that earlier-turn facts are reused: the on-call
   identity from turn 2 carries over without re-fetching.
4. **Turn 4 — page on-call to follow up** (PagerDuty incident).
   Combines all three accumulated facts (BigQuery rowset summary,
   Linear ticket, Slack thread) in one answer prompt.

Across turns the script measures:

- Cumulative facts written to the store (grows).
- Per-turn answer-phase token count (bounded — old turns get
  compacted into facts, not re-injected raw).
- The artifact handle from turn 1 survives unchanged into turn 4's
  answer prompt (proves dependency closure works across turns).

This is **simulated**: each tool returns a canned MCP-shaped result, no
real backend is touched. The scripts uses real contextweaver primitives —
:class:`Router`, :class:`ContextManager`, ``ingest_mcp_result``, and the
public schema-hydration helper from issue #261.

Run standalone::

    python examples/architectures/mcp_context_gateway/main_multi.py

Or via ``make architectures`` / ``make example``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from contextweaver.config import ContextBudget
from contextweaver.context.manager import ContextManager
from contextweaver.data import gateway_catalog_path
from contextweaver.routing.cards import make_choice_cards
from contextweaver.routing.catalog import Catalog, load_catalog_yaml
from contextweaver.routing.hydration import SchemaSource, hydrate_with_schema
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import ContextItem, ItemKind, Phase

# Issue #264: the 60-tool gateway catalog ships inside the wheel; the
# multi-turn variant reads it through ``gateway_catalog_path()`` so it
# also works from a ``pip install`` without the ``examples/`` directory.
CATALOG_PATH = gateway_catalog_path()
SCHEMAS_PATH = Path(__file__).parent / "tool_schemas.json"


# Each turn: (user_text, routing_query, intent_tool_id, args, canned_result).
# Keeping the intent + args explicit (rather than running an LLM) makes the
# scenario deterministic without hiding the per-turn decision boundary.
TRANSCRIPT: list[tuple[str, str, str, dict[str, Any], dict[str, Any]]] = [
    (
        "Why did customer C-12345's MRR drop last month?",
        "Execute a BigQuery query to find MRR delta rows for customer C-12345",
        "bigquery.run_query",
        {
            "sql": (
                "SELECT date, plan, mrr_delta_usd, reason_code, actor, notes "
                "FROM `ops-analytics-prod.billing.mrr_changes` "
                "WHERE customer_id = 'C-12345' "
                "AND date BETWEEN '2026-02-01' AND '2026-04-30' "
                "ORDER BY date"
            ),
            "max_results": 1000,
        },
        # Populated lazily by `_bigquery_result()` below.
        {},
    ),
    (
        "Who handled the downgrade? Pull the related Linear ticket.",
        "Search Linear tickets matching customer C-12345 plan change",
        "linear.tickets.search",
        {"query": "C-12345 plan change downgrade", "limit": 1},
        {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "ticket TKT-742 — 'Self-serve downgrade for C-12345 (Growth -> "
                        "Starter)'\nstatus: closed\nowner: alice@example.com\n"
                        "closed_at: 2026-03-18T17:42Z\nlink: https://linear.app/ops/issue/TKT-742"
                    ),
                }
            ],
            "isError": False,
        },
    ),
    (
        "Notify alice in Slack — she should see the rowset I pulled.",
        "Send a direct Slack message to alice with the artifact link",
        "slack.dm.send",
        {
            "user": "alice@example.com",
            "text": (
                "Heads-up: surfaced your C-12345 plan-change ticket — see attached "
                "rowset artifact for the day-by-day breakdown."
            ),
        },
        {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "ok — message posted to @alice (thread_ts=1716309042.000300, "
                        "channel=alice-dm)"
                    ),
                }
            ],
            "isError": False,
        },
    ),
    (
        "Acknowledge the existing PagerDuty incident so on-call knows we're handling it.",
        "Acknowledge the existing PagerDuty incident for C-12345 downgrade",
        "pagerduty.incidents.ack",
        {
            "incident_id": "PD-9931",
            "acknowledger": "ops-copilot",
        },
        {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "incident PD-9931 acknowledged by ops-copilot "
                        "(service=billing-ops urgency=low)"
                    ),
                }
            ],
            "isError": False,
        },
    ),
]


def _bigquery_result() -> dict[str, Any]:
    """The same 16 KB rowset the single-turn variant uses."""
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
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def _build_router(catalog: Catalog) -> Router:
    """Build the routing engine. top_k=5 mirrors the single-turn variant."""
    items = catalog.all()
    graph = TreeBuilder(max_children=10).build(items)
    return Router(graph, items=items, top_k=5)


def _select_from_shortlist(shortlist: list[str], intent: str) -> str:
    return intent if intent in shortlist else shortlist[0]


def _fact_summary_for_turn(turn_idx: int, chosen: str, envelope_text: str) -> tuple[str, str]:
    """Turn-specific durable summary written to ``mgr.add_fact_sync``.

    Each turn writes a single fact that captures the load-bearing decision
    so the answer prompt for later turns does not need to re-read the raw
    upstream payload.
    """
    if turn_idx == 1:
        return (
            "customer.C-12345.plan_change",
            "growth -> starter (self-serve, day 47, -$450 MRR)",
        )
    if turn_idx == 2:
        return ("customer.C-12345.ticket", "TKT-742 closed by alice@example.com on 2026-03-18")
    if turn_idx == 3:
        return ("customer.C-12345.slack_thread", "alice notified (thread_ts=1716309042.000300)")
    if turn_idx == 4:
        return ("customer.C-12345.followup", "PagerDuty PD-9931 acknowledged (urgency=low)")
    return (f"turn.{turn_idx}.summary", envelope_text[:60])


def main() -> None:
    """Run the 4-turn MCP Context Gateway scenario end-to-end."""
    _print_header("contextweaver -- MCP Context Gateway (MULTI-TURN, 4 turns)")

    # ------------------------------------------------------------------
    # 1. Load the 60-tool catalog and build a routing graph.
    # ------------------------------------------------------------------
    catalog = Catalog()
    for selectable in load_catalog_yaml(CATALOG_PATH):
        catalog.register(selectable)
    catalog_tools = len(catalog.all())
    schemas = SchemaSource.from_json_file(SCHEMAS_PATH)

    router = _build_router(catalog)
    budget = ContextBudget(route=1500, call=2000, interpret=3000, answer=4000)
    mgr = ContextManager(budget=budget)

    print(f"Loaded catalog: {catalog_tools} tools")
    print(f"Schema sidecar: {len(schemas.known_ids())} tools with inline schemas\n")

    intent_hits = 0
    answer_token_history: list[int] = []
    fact_count_history: list[int] = []
    artifact_handles: list[str] = []

    # ------------------------------------------------------------------
    # Turn-by-turn loop. Each turn ingests user → routes → chooses tool →
    # hydrates schema → calls upstream → ingests result → writes a fact →
    # builds the answer prompt.
    # ------------------------------------------------------------------
    for turn_idx, (user_text, routing_query, intent, args, canned) in enumerate(
        TRANSCRIPT, start=1
    ):
        _print_header(f"Turn {turn_idx}")
        print(f"user:    {user_text}")
        print(f"routing: {routing_query}")

        # Lazy: turn 1 uses the large BigQuery body.
        result_payload = _bigquery_result() if turn_idx == 1 else canned

        u_id = f"u{turn_idx}"
        mgr.ingest_sync(ContextItem(id=u_id, kind=ItemKind.user_turn, text=user_text))

        # Route — bounded shortlist of 5.
        route = router.route(routing_query)
        shortlist = route.candidate_ids
        chosen = _select_from_shortlist(shortlist, intent)
        if intent in shortlist:
            intent_hits += 1
        cards = make_choice_cards(
            route.candidate_items,
            scores=dict(zip(shortlist, route.scores, strict=False)),
        )
        print(f"shortlist: {shortlist}")
        print(f"chosen:   {chosen}  (intent={intent!r}, shortlist_hit={intent in shortlist})")
        print(f"cards:    {len(cards)} (no full schemas in the prompt)")

        # Hydrate — sidecar schema if registered, else empty (most turns).
        hydrated = hydrate_with_schema(catalog, chosen, schemas)
        if hydrated.args_schema:
            print(f"schema:   {chosen}  ({len(json.dumps(hydrated.args_schema))} chars)")
        else:
            print(f"schema:   {chosen}  (no sidecar schema — empty args_schema)")

        # Tool call + ingest_mcp_result (firewall runs on large payloads).
        tc_id = f"tc{turn_idx}"
        mgr.ingest_sync(
            ContextItem(
                id=tc_id,
                kind=ItemKind.tool_call,
                text=f"{chosen}({json.dumps(args, separators=(',', ':'))[:60]}...)",
                parent_id=u_id,
            )
        )
        item, envelope = mgr.ingest_mcp_result(
            tool_call_id=tc_id,
            mcp_result=result_payload,
            tool_name=chosen,
            firewall_threshold=2000,
        )
        injected_chars = len(item.text)
        raw_chars = len(result_payload["content"][0]["text"])
        handle = item.artifact_ref.handle if item.artifact_ref else "<inline>"
        if item.artifact_ref:
            artifact_handles.append(handle)
        print(f"firewall: {raw_chars:,} chars -> {injected_chars} chars  (artifact={handle})")

        # Write the per-turn durable fact.
        key, value = _fact_summary_for_turn(turn_idx, chosen, item.text)
        mgr.add_fact_sync(key=key, value=value, metadata={"source": chosen, "turn": turn_idx})

        # Build the answer prompt for this turn.
        answer = mgr.build_sync(phase=Phase.answer, query=routing_query)
        answer_token_history.append(answer.stats.prompt_tokens)
        fact_count_history.append(len(list(mgr.fact_store.all())))
        print(
            f"answer:   included={answer.stats.included_count}  "
            f"tokens={answer.stats.prompt_tokens}  "
            f"facts_after_turn={fact_count_history[-1]}"
        )

    # ------------------------------------------------------------------
    # Cross-turn invariants — these are the "compose under conversational
    # pressure" claims the multi-turn variant exists to prove.
    # ------------------------------------------------------------------
    _print_header("Multi-turn invariants")
    print(f"turns                       = {len(TRANSCRIPT)}")
    print(f"intent_in_shortlist_count   = {intent_hits} / {len(TRANSCRIPT)}")
    print(f"answer_tokens_per_turn      = {answer_token_history}")
    print(f"facts_per_turn              = {fact_count_history}")
    print(f"artifact_handles_persisted  = {artifact_handles}")
    # The turn-1 artifact should survive into the final answer prompt
    # because the answer build pulls dependency-linked tool results and
    # the artifact ref is anchored on the turn-1 tool_result.
    final_answer = mgr.build_sync(phase=Phase.answer, query=TRANSCRIPT[-1][1])
    survived = artifact_handles and artifact_handles[0] in final_answer.prompt
    print(
        f"turn1_artifact_in_final_prompt = "
        f"{'yes' if survived else 'no  (regression — dependency closure broke)'}"
    )
    print(
        f"final_prompt_tokens          = {final_answer.stats.prompt_tokens}  "
        f"chars={len(final_answer.prompt):,}"
    )


if __name__ == "__main__":
    main()

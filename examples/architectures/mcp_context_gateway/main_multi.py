"""MCP Context Gateway — multi-turn variant (issue #262).

Sibling of :mod:`main` that runs the same 60-tool gateway through five
consecutive turns of a single conversation. The point of this variant is to
exercise three properties of the gateway that a single-turn run cannot show:

1. **Routing stability** — re-running the router against the same catalog
   on each turn returns the right tool every time without state leak.
2. **Cross-turn fact accumulation** — :func:`ContextManager.add_fact_sync`
   facts from earlier turns are visible to the answer-phase prompt of later
   turns.
3. **Cumulative firewall reduction** — the firewall fires on more than one
   turn (Turns 1 and 5 are the heavy-output turns) and the total raw bytes
   never enter the answer-phase prompts that follow.

The transcript follows the script outlined in issue #262:

1. Investigate MRR drop → ``bigquery.run_query`` (~16 KB rowset → firewall).
2. Who handled it?         → ``crm.notes.create``.
3. Notify the customer     → ``email.draft.create`` *(not in catalog, mapped to closest)*.
4. Open a tracking ticket  → ``linear.tickets.create``.
5. Post a summary          → ``slack.channels.post``.

Run standalone::

    python examples/architectures/mcp_context_gateway/main_multi.py

Or via ``make architectures``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from contextweaver.config import ContextBudget
from contextweaver.context.manager import ContextManager
from contextweaver.data import gateway_catalog_path
from contextweaver.routing.cards import make_choice_cards, render_cards_text
from contextweaver.routing.catalog import Catalog, load_catalog_yaml
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import ContextItem, ItemKind, Phase

CATALOG_PATH = gateway_catalog_path()


@dataclass(frozen=True)
class _Turn:
    """One step in the multi-turn transcript.

    Fields:
        user_query: The user-facing question or instruction.
        routing_query: Tool-shaped routing query (a production agent would
            LLM-rephrase the user query into this).
        intent: Preferred tool id; falls back to top-1 if absent from the
            shortlist.
        tool_result_text: Canned upstream result text for the chosen tool.
            ~20 KB on Turns 1 and 5 to exercise the firewall.
        fact_key: Optional fact key persisted via ``add_fact_sync`` after
            the firewall runs.
        fact_value: Value paired with ``fact_key``.
    """

    user_query: str
    routing_query: str
    intent: str
    tool_result_text: str
    fact_key: str | None = None
    fact_value: str | None = None


def _print_header(title: str) -> None:
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def _mock_bigquery_rowset() -> str:
    """Identical to :mod:`main`'s rowset — kept in lockstep for cross-variant comparison."""
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


def _mock_slack_thread() -> str:
    """20 KB synthetic Slack thread for Turn 5's heavy upstream output."""
    msgs = []
    for i in range(1, 121):
        msgs.append(
            json.dumps(
                {
                    "ts": f"170000{i:04d}.000{i:03d}",
                    "user": "U-OPS" if i % 5 else "U-CSM",
                    "channel": "#incidents",
                    "text": (
                        "ack: tracking the MRR delta incident for C-12345 "
                        f"(turn {i}, looped 30s monitor still firing); "
                        "ETA 10 minutes on the dashboard refresh."
                    ),
                },
                sort_keys=True,
            )
        )
    return "channel: #incidents\nmessages_returned: 120\n\n" + "\n".join(msgs) + "\n"


_TURNS: tuple[_Turn, ...] = (
    _Turn(
        user_query="Why did customer C-12345's MRR drop last month?",
        routing_query=("Execute a BigQuery query to find MRR delta rows for customer C-12345"),
        intent="bigquery.run_query",
        tool_result_text=_mock_bigquery_rowset(),
        fact_key="customer.C-12345.plan_change",
        fact_value="growth -> starter (self-serve, day 47, -$450 MRR)",
    ),
    _Turn(
        user_query="Who at the customer side handled this — was it self-serve or CSM-mediated?",
        routing_query="Attach a CRM note recording the MRR drop incident for account C-12345",
        intent="crm.notes.create",
        tool_result_text=(
            "note_id: NOTE-9821\n"
            "account_id: C-12345\n"
            "title: 'MRR drop investigation'\n"
            "body: 'self-serve plan change recorded on day 47'\n"
            "actor: 'system'\n"
        ),
        fact_key="customer.C-12345.contact_owner",
        fact_value="self-serve (no CSM intervention)",
    ),
    _Turn(
        user_query=(
            "Notify the customer that we noticed the change and offered a downgrade-survey link."
        ),
        routing_query="Draft an email to a customer about a recent plan change",
        intent="email.send",
        tool_result_text=(
            "email_id: EML-1142\n"
            "to: 'contact@C-12345'\n"
            "subject: 'Following up on your recent plan change'\n"
            "status: 'sent'\n"
        ),
    ),
    _Turn(
        user_query="Open a tracking ticket so we can revisit this in the next QBR.",
        routing_query="Create a Linear ticket to track the MRR drop investigation",
        intent="linear.tickets.create",
        tool_result_text=(
            "ticket_id: LIN-2031\n"
            "title: 'Revisit C-12345 plan change at next QBR'\n"
            "status: 'open'\n"
            "owner: 'ops-team'\n"
        ),
        fact_key="incident.C-12345.tracking_ticket",
        fact_value="LIN-2031 (open)",
    ),
    _Turn(
        user_query="Post a summary of what we found to #incidents so the team can review.",
        routing_query="Post a summary message to the #incidents Slack channel",
        intent="slack.channels.post",
        tool_result_text=_mock_slack_thread(),
        fact_key="incident.C-12345.broadcast",
        fact_value="posted to #incidents (Slack thread 120 msgs reviewed)",
    ),
)


def _build_router(catalog: Catalog) -> Router:
    items = catalog.all()
    graph = TreeBuilder(max_children=10).build(items)
    return Router(graph, items=items, top_k=5)


def _select_from_shortlist(shortlist: list[str], intent: str) -> str:
    if intent in shortlist:
        return intent
    return shortlist[0]


def _run_turn(n: int, turn: _Turn, mgr: ContextManager, router: Router) -> tuple[int, int, int]:
    """Run one turn and return ``(raw_chars, summary_chars, prompt_tokens)``."""
    _print_header(f"Turn {n} — {turn.user_query[:60]}")
    print(f"user typed:    {turn.user_query!r}")
    print(f"routing query: {turn.routing_query!r}")

    u_id = f"u{n}"
    mgr.ingest_sync(ContextItem(id=u_id, kind=ItemKind.user_turn, text=turn.user_query))

    result = router.route(turn.routing_query)
    shortlist = result.candidate_ids
    chosen = _select_from_shortlist(shortlist, turn.intent)
    cards = make_choice_cards(
        result.candidate_items,
        scores=dict(zip(shortlist, result.scores, strict=False)),
    )
    print(f"shortlist ({len(shortlist)}): {shortlist}")
    print(f"chosen:    {chosen}  (intent={turn.intent!r})")
    print(
        f"\nChoiceCards ({len(cards)} cards, {sum(len(c.description) for c in cards)} desc-chars):"
    )
    print(render_cards_text(cards))

    tc_id = f"tc{n}"
    mgr.ingest_sync(
        ContextItem(
            id=tc_id,
            kind=ItemKind.tool_call,
            text=f"{chosen}(...)",
            parent_id=u_id,
        )
    )
    raw_text = turn.tool_result_text
    raw_chars = len(raw_text)
    item, _envelope = mgr.ingest_mcp_result(
        tool_call_id=tc_id,
        mcp_result={"content": [{"type": "text", "text": raw_text}], "isError": False},
        tool_name=chosen,
        firewall_threshold=2000,
    )
    summary_chars = len(item.text)
    artifact_handle = item.artifact_ref.handle if item.artifact_ref else "<none>"
    print(
        f"\nfirewall: {raw_chars:,} chars  ->  {summary_chars}-char summary  "
        f"(artifact {artifact_handle})"
    )

    if turn.fact_key and turn.fact_value:
        mgr.add_fact_sync(
            key=turn.fact_key,
            value=turn.fact_value,
            metadata={"source": chosen, "turn": n},
        )
        print(f"persisted fact: {turn.fact_key} = {turn.fact_value!r}")

    answer = mgr.build_sync(phase=Phase.answer, query=turn.routing_query)
    prompt_tokens = answer.stats.prompt_tokens
    print(f"\nanswer prompt tokens: {prompt_tokens}")

    # Per-turn invariant: the deepest fragment of Turn 1's rowset must
    # never appear in any later turn's prompt — that's the cumulative
    # firewall guarantee the issue's tests want to pin.
    rowset_sentinel = '"mrr_delta_usd": -450'
    leaked = rowset_sentinel in answer.prompt
    print(f"contains raw Turn-1 rowset? {'YES (regression!)' if leaked else 'no'}")

    return raw_chars, summary_chars, prompt_tokens


def main() -> None:
    """Run the 5-turn MCP Context Gateway scenario end-to-end."""
    _print_header("contextweaver -- MCP Context Gateway (multi-turn variant)")
    print("(5-turn transcript exercising cross-turn fact accumulation)")

    catalog = Catalog()
    for item in load_catalog_yaml(CATALOG_PATH):
        catalog.register(item)
    print(f"\nLoaded catalog: {len(catalog.all())} tools")

    router = _build_router(catalog)
    budget = ContextBudget(route=1500, call=2000, interpret=3000, answer=4000)
    mgr = ContextManager(budget=budget)

    raw_total = 0
    summary_total = 0
    per_turn_tokens: list[int] = []
    for n, turn in enumerate(_TURNS, 1):
        raw, summary, tokens = _run_turn(n, turn, mgr, router)
        raw_total += raw
        summary_total += summary
        per_turn_tokens.append(tokens)

    _print_header("Cumulative metrics across all turns")
    cumulative_reduction = 100.0 * (1.0 - summary_total / max(raw_total, 1))
    print(f"turns                       = {len(_TURNS)}")
    print(f"raw_upstream_chars_total    = {raw_total:,}")
    print(f"injected_summary_chars_total= {summary_total:,}")
    print(f"cumulative_firewall_pct     = {cumulative_reduction:.1f}%")
    print(f"per_turn_prompt_tokens      = {per_turn_tokens}")
    persisted_facts = sorted(mgr.fact_store.all(), key=lambda f: f.key)
    print(f"persisted_facts_count       = {len(persisted_facts)}")
    for fact in persisted_facts:
        print(f"  - {fact.key}: {fact.value}")
    artifact_count = len(list(mgr.artifact_store.list_refs()))
    print(f"artifacts_stored            = {artifact_count}")


if __name__ == "__main__":
    main()

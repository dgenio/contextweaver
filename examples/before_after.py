"""Before/after comparison: raw context vs. contextweaver-managed context.

Shows the same agent loop executed two ways:
  WITHOUT contextweaver — raw text concatenation that bloats the prompt
  WITH contextweaver    — phase-specific budgeted compilation

Compares token counts to demonstrate how contextweaver keeps the prompt
under budget while preserving the information the LLM actually needs.
"""

from __future__ import annotations

import os
import sys

# Allow running from the repo root without installing.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from contextweaver.config import ContextBudget
from contextweaver.context.manager import ContextManager
from contextweaver.types import ContextItem, ItemKind, Phase


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: len(text) // 4."""
    return len(text) // 4


def _make_big_tool_result() -> str:
    """Simulate a large tool result (a database query returning many rows)."""
    lines = ["Query Results (customers table)\n" + "=" * 40]
    for i in range(1, 101):
        lines.append(
            f"Row {i:>3}: id={1000 + i}  name=Customer_{i:03d}  "
            f"email=customer{i}@example.com  "
            f"balance=${i * 99.99:.2f}  "
            f"status={'active' if i % 5 != 0 else 'churned'}  "
            f"region={'NA' if i % 3 == 0 else 'EMEA' if i % 3 == 1 else 'APAC'}  "
            f"signup=2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"
        )
    lines.append("\nTotal: 100 rows  |  Active: 80  |  Churned: 20")
    return "\n".join(lines)


def main() -> None:
    user_msg = "How many active customers do we have and what is their total balance?"
    agent_msg = "I will query the customers table to find active customer counts and balances."
    tool_call = (
        'db_query(sql="SELECT status, COUNT(*), SUM(balance) FROM customers GROUP BY status")'
    )
    tool_result = _make_big_tool_result()

    # ================================================================== #
    # WITHOUT contextweaver: naive concatenation                         #
    # ================================================================== #
    print("=" * 60)
    print("WITHOUT CONTEXTWEAVER (raw concatenation)")
    print("=" * 60)

    raw_prompt = (
        f"User: {user_msg}\n\n"
        f"Assistant: {agent_msg}\n\n"
        f"Tool call: {tool_call}\n\n"
        f"Tool result:\n{tool_result}\n\n"
        f"Now answer the user's question."
    )

    raw_tokens = _estimate_tokens(raw_prompt)
    print(f"  Prompt length: {len(raw_prompt)} chars")
    print(f"  Token estimate: ~{raw_tokens}")
    print(
        f"  Tool result alone: {len(tool_result)} chars (~{_estimate_tokens(tool_result)} tokens)"
    )
    print()

    # ================================================================== #
    # WITH contextweaver: phase-specific budgeted compilation            #
    # ================================================================== #
    print("=" * 60)
    print("WITH CONTEXTWEAVER (budgeted, firewalled)")
    print("=" * 60)

    # Use a tight budget to show the contrast
    budget = ContextBudget(route=500, call=800, interpret=1000, answer=1200)
    mgr = ContextManager(budget=budget)

    # Ingest the same events
    mgr.ingest_sync(
        ContextItem(
            id="u1",
            kind=ItemKind.USER_TURN,
            text=user_msg,
            token_estimate=_estimate_tokens(user_msg),
        )
    )
    mgr.ingest_sync(
        ContextItem(
            id="a1",
            kind=ItemKind.AGENT_MSG,
            text=agent_msg,
            token_estimate=_estimate_tokens(agent_msg),
        )
    )
    mgr.ingest_sync(
        ContextItem(
            id="tc1",
            kind=ItemKind.TOOL_CALL,
            text=tool_call,
            token_estimate=_estimate_tokens(tool_call),
            parent_id="u1",
        )
    )

    # Ingest the large tool result THROUGH THE FIREWALL
    item, envelope = mgr.ingest_tool_result_sync(
        tool_call_id="tc1",
        raw_output=tool_result,
        tool_name="db_query",
        firewall_threshold=500,
    )

    # Add a fact for good measure
    mgr.add_fact_sync("user_intent", "count active customers and sum balances")

    # Build context for ANSWER phase
    pack = mgr.build_sync(
        goal="Answer about active customer count and total balance",
        phase=Phase.ANSWER,
    )

    cw_tokens = pack.budget_used
    print(f"  Prompt length: {len(pack.rendered_text)} chars")
    print(f"  Token estimate: ~{cw_tokens}")
    print(f"  Budget total:   {pack.budget_total}")
    print(f"  Items included: {pack.stats.included_count}")
    print(f"  Items dropped:  {pack.stats.dropped_count}")
    print(f"  Dedup removed:  {pack.stats.dedup_removed}")
    print()

    # Show the firewalled summary vs. the raw output
    print("  Firewall effect:")
    print(
        f"    Raw tool output:       {len(tool_result)} chars (~{_estimate_tokens(tool_result)} tokens)"
    )
    print(f"    Summarized to:         {len(item.text)} chars (~{item.token_estimate} tokens)")
    print(f"    Artifact stored:       {item.artifact_ref}")
    print(f"    Facts extracted:       {envelope.facts}")
    print()

    # ================================================================== #
    # COMPARISON                                                         #
    # ================================================================== #
    print("=" * 60)
    print("COMPARISON")
    print("=" * 60)
    savings = raw_tokens - cw_tokens
    pct = (savings / raw_tokens * 100) if raw_tokens > 0 else 0
    print(f"  Raw approach:         ~{raw_tokens} tokens")
    print(f"  contextweaver:        ~{cw_tokens} tokens")
    print(f"  Tokens saved:         ~{savings} ({pct:.0f}%)")
    print(
        f"  Within budget:        {'Yes' if cw_tokens <= pack.budget_total else 'No'} "
        f"({cw_tokens} <= {pack.budget_total})"
    )
    print()

    # Show what the LLM would actually see
    print("=" * 60)
    print("WHAT THE LLM SEES (contextweaver prompt)")
    print("=" * 60)
    print(pack.rendered_text[:800])
    if len(pack.rendered_text) > 800:
        print(f"  ... ({len(pack.rendered_text) - 800} more chars)")


if __name__ == "__main__":
    main()

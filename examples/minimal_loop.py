"""Minimal agent loop example.

Demonstrates the four-phase contextweaver usage pattern:
  ROUTE  -> choose which tool/agent to invoke
  CALL   -> prepare the tool invocation
  INTERPRET -> process and interpret the tool result
  ANSWER -> compile the final answer for the user

Creates a ContextManager, ingests items using sync wrappers, builds a
context pack for each phase, and prints the rendered prompt.
"""

from __future__ import annotations

import os
import sys

# Allow running from the repo root without installing.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from contextweaver.context.manager import ContextManager
from contextweaver.types import ContextItem, ItemKind, Phase


def main() -> None:
    # ------------------------------------------------------------------ #
    # 1. Create a ContextManager (all stores default to in-memory)       #
    # ------------------------------------------------------------------ #
    mgr = ContextManager()

    # ------------------------------------------------------------------ #
    # 2. Simulate an agent loop by ingesting events                      #
    # ------------------------------------------------------------------ #
    mgr.ingest_sync(
        ContextItem(
            id="u1",
            kind=ItemKind.USER_TURN,
            text="How many rows are in the users table?",
            token_estimate=10,
        )
    )

    # Store a durable fact about the user's intent
    mgr.add_fact_sync("user_intent", "count rows in users table")

    # Store an episodic summary from a prior conversation
    mgr.add_episode_sync("ep1", "User previously asked about the schema of the users table.")

    # ------------------------------------------------------------------ #
    # Phase 1 — ROUTE: decide which tool to call                         #
    # ------------------------------------------------------------------ #
    mgr.ingest_sync(
        ContextItem(
            id="plan1",
            kind=ItemKind.PLAN_STATE,
            text="Plan: query the database to count rows in the users table.",
            token_estimate=14,
        )
    )

    route_pack = mgr.build_sync(
        goal="Determine which tool can count rows in a database table",
        phase=Phase.ROUTE,
    )
    print("=" * 60)
    print("PHASE: ROUTE")
    print("=" * 60)
    print(route_pack.rendered_text[:500])
    print(f"\n  Budget used / total: {route_pack.budget_used} / {route_pack.budget_total}")
    print(f"  Included items: {route_pack.stats.included_count}")
    print()

    # ------------------------------------------------------------------ #
    # Phase 2 — CALL: prepare the tool invocation                        #
    # ------------------------------------------------------------------ #
    mgr.ingest_sync(
        ContextItem(
            id="a1",
            kind=ItemKind.AGENT_MSG,
            text="I will query the database for you.",
            token_estimate=8,
        )
    )
    mgr.ingest_sync(
        ContextItem(
            id="tc1",
            kind=ItemKind.TOOL_CALL,
            text='db_query(sql="SELECT COUNT(*) FROM users")',
            token_estimate=10,
            metadata={"tool_name": "db_query"},
            parent_id="u1",
        )
    )

    call_pack = mgr.build_sync(
        goal="Execute db_query to count users",
        phase=Phase.CALL,
    )
    print("=" * 60)
    print("PHASE: CALL")
    print("=" * 60)
    print(call_pack.rendered_text[:500])
    print(f"\n  Budget used / total: {call_pack.budget_used} / {call_pack.budget_total}")
    print(f"  Included items: {call_pack.stats.included_count}")
    print()

    # ------------------------------------------------------------------ #
    # Phase 3 — INTERPRET: process the tool result                       #
    # ------------------------------------------------------------------ #
    mgr.ingest_sync(
        ContextItem(
            id="tr1",
            kind=ItemKind.TOOL_RESULT,
            text="count: 1042",
            token_estimate=3,
            parent_id="tc1",
        )
    )

    interpret_pack = mgr.build_sync(
        goal="Interpret the database query result",
        phase=Phase.INTERPRET,
    )
    print("=" * 60)
    print("PHASE: INTERPRET")
    print("=" * 60)
    print(interpret_pack.rendered_text[:500])
    print(f"\n  Budget used / total: {interpret_pack.budget_used} / {interpret_pack.budget_total}")
    print(f"  Included items: {interpret_pack.stats.included_count}")
    print()

    # ------------------------------------------------------------------ #
    # Phase 4 — ANSWER: compile the final response                       #
    # ------------------------------------------------------------------ #
    answer_pack = mgr.build_sync(
        goal="Answer the user about how many rows are in the users table",
        phase=Phase.ANSWER,
    )
    print("=" * 60)
    print("PHASE: ANSWER")
    print("=" * 60)
    print(answer_pack.rendered_text[:800])
    print(f"\n  Budget used / total: {answer_pack.budget_used} / {answer_pack.budget_total}")
    print(f"  Included items: {answer_pack.stats.included_count}")
    print(f"  Dropped items:  {answer_pack.stats.dropped_count}")
    print(f"  Dedup removed:  {answer_pack.stats.dedup_removed}")
    print(f"  Facts snapshot:  {answer_pack.facts_snapshot}")
    print(f"  Episodic summaries: {answer_pack.episodic_summaries}")
    print()

    # ------------------------------------------------------------------ #
    # Inspect BuildStats to understand what the engine did                #
    # ------------------------------------------------------------------ #
    print("=" * 60)
    print("BUILD STATS (ANSWER phase)")
    print("=" * 60)
    stats = answer_pack.stats
    print(f"  Total candidates considered: {stats.total_candidates}")
    print(f"  Included in context:         {stats.included_count}")
    print(f"  Dropped from context:        {stats.dropped_count}")
    print(f"  Dropped reasons:             {stats.dropped_reasons}")
    print(f"  Dedup removed:               {stats.dedup_removed}")
    print(f"  Dependency closures:         {stats.dependency_closures}")
    print(f"  Tokens per section:          {stats.tokens_per_section}")


if __name__ == "__main__":
    main()

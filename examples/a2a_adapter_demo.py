"""A2A (Agent-to-Agent) adapter demo.

Demonstrates the A2A integration pattern:
  1. Convert A2A agent descriptors to SelectableItems
  2. Convert A2A agent responses to ResultEnvelopes
  3. Load a recorded A2A session from a JSONL file
  4. Ingest the session into a ContextManager
  5. Build context and show phase-specific projections
"""

from __future__ import annotations

import os
import sys

# Allow running from the repo root without installing.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from contextweaver.adapters.a2a import (
    agent_response_to_envelope,
    agent_to_item,
    load_a2a_session_jsonl,
)
from contextweaver.context.manager import ContextManager
from contextweaver.types import Phase

# Path to the example A2A session data
_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_SESSION_FILE = os.path.join(_DATA_DIR, "a2a_session.jsonl")


def main() -> None:
    # ------------------------------------------------------------------ #
    # 1. Convert A2A agent descriptors to SelectableItems                #
    # ------------------------------------------------------------------ #
    print("=" * 60)
    print("A2A AGENT DESCRIPTORS -> SelectableItems")
    print("=" * 60)

    agents = [
        {
            "name": "DataAgent",
            "description": "Retrieves and processes structured data from databases and APIs.",
            "skills": [
                {"id": "db_query", "name": "Database Query"},
                {"id": "data_transform", "name": "Data Transform"},
                {"id": "csv_export", "name": "CSV Export"},
            ],
        },
        {
            "name": "CommsAgent",
            "description": "Drafts and sends communications including emails and reports.",
            "skills": [
                {"id": "email_draft", "name": "Email Draft"},
                {"id": "report_gen", "name": "Report Generation"},
            ],
        },
        {
            "name": "AnalyticsAgent",
            "description": "Runs business analytics queries and generates insights.",
            "skills": [
                {"id": "kpi_analysis", "name": "KPI Analysis"},
                {"id": "trend_detect", "name": "Trend Detection"},
            ],
        },
    ]

    for agent_info in agents:
        item = agent_to_item(agent_info)
        print(f"  {item.id:25s}  kind={item.kind:6s}  tags={item.tags}")
    print()

    # ------------------------------------------------------------------ #
    # 2. Convert an A2A agent response to a ResultEnvelope               #
    # ------------------------------------------------------------------ #
    print("=" * 60)
    print("A2A AGENT RESPONSE -> ResultEnvelope")
    print("=" * 60)

    response = {
        "status": "ok",
        "text": (
            "Sales Report Q3 2024:\n"
            "- Total Revenue: $2.4M (up 15% from Q2)\n"
            "- New Customers: 42\n"
            "- Churn Rate: 3.2%\n"
            "- Top Product: Enterprise Plan ($1.1M)"
        ),
    }

    envelope = agent_response_to_envelope(response)
    print(f"  Status:  {envelope.status}")
    print(f"  Summary: {envelope.summary[:200]}")
    print(f"  Facts:   {envelope.facts}")
    print()

    # ------------------------------------------------------------------ #
    # 3. Load an A2A session from JSONL                                  #
    # ------------------------------------------------------------------ #
    print("=" * 60)
    print("LOAD A2A SESSION FROM JSONL")
    print("=" * 60)

    items = load_a2a_session_jsonl(_SESSION_FILE)
    print(f"  Loaded {len(items)} context items from {os.path.basename(_SESSION_FILE)}")
    for ci in items:
        source = ci.metadata.get("source", "?")
        parent_info = f" (parent={ci.parent_id})" if ci.parent_id else ""
        print(
            f"    {ci.id:6s}  {ci.kind.value:12s}  source={source:14s}"
            f"  tokens~{ci.token_estimate:>3d}{parent_info}"
        )
    print()

    # ------------------------------------------------------------------ #
    # 4. Ingest into ContextManager                                      #
    # ------------------------------------------------------------------ #
    print("=" * 60)
    print("INGEST + BUILD CONTEXT")
    print("=" * 60)

    mgr = ContextManager()
    for ci in items:
        mgr.ingest_sync(ci)

    # Add semantic facts about the multi-agent session
    mgr.add_fact_sync("session_type", "multi-agent orchestration")
    mgr.add_fact_sync("agents_involved", "DataAgent, CommsAgent")
    mgr.add_fact_sync("user_goal", "Summarize sales report and draft email")

    # Add an episodic summary
    mgr.add_episode_sync(
        "ep1",
        "DataAgent retrieved Q3 sales data, CommsAgent drafted team email.",
    )

    # Build context for each phase and show differences
    for phase in [Phase.ROUTE, Phase.CALL, Phase.INTERPRET, Phase.ANSWER]:
        pack = mgr.build_sync(
            goal="Summarize Q3 sales and draft email to team",
            phase=phase,
        )
        print(f"\n  Phase: {phase.value}")
        print(f"    Budget:          {pack.budget_used} / {pack.budget_total} tokens")
        print(f"    Included items:  {pack.stats.included_count}")
        print(f"    Dropped items:   {pack.stats.dropped_count}")
        if pack.stats.dropped_reasons:
            print(f"    Drop reasons:    {pack.stats.dropped_reasons}")

    # ------------------------------------------------------------------ #
    # 5. Show the full ANSWER context                                    #
    # ------------------------------------------------------------------ #
    print()
    print("=" * 60)
    print("FULL ANSWER CONTEXT")
    print("=" * 60)

    answer_pack = mgr.build_sync(
        goal="Summarize Q3 sales and draft email to team",
        phase=Phase.ANSWER,
    )
    print(answer_pack.rendered_text[:1000])
    print()
    print(f"  Facts snapshot:       {answer_pack.facts_snapshot}")
    print(f"  Episodic summaries:   {answer_pack.episodic_summaries}")


if __name__ == "__main__":
    main()

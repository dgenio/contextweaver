"""A2A adapter demo.

Demonstrates converting A2A agent cards and task results into
contextweaver-native types, and ingesting an A2A session from a JSONL file
with summarization and projection.
"""

from __future__ import annotations

from pathlib import Path

from contextweaver.adapters.a2a import (
    a2a_agent_to_selectable,
    a2a_result_to_envelope,
    load_a2a_session_jsonl,
)
from contextweaver.context.manager import ContextManager
from contextweaver.summarize.rules import RuleBasedSummarizer
from contextweaver.types import ItemKind, Phase

A2A_AGENT_CARD = {
    "name": "DataAgent",
    "description": "Retrieves and processes structured data from databases.",
    "skills": [
        {"id": "db_query", "name": "Database Query"},
        {"id": "data_transform", "name": "Data Transform"},
    ],
    "defaultInputModes": ["text"],
    "defaultOutputModes": ["text", "data"],
    "url": "https://agents.example.com/data",
}

A2A_TASK_RESULT = {
    "status": {"state": "completed", "message": "Query executed successfully"},
    "artifacts": [
        {
            "name": "result",
            "parts": [
                {
                    "type": "text",
                    "text": (
                        "Query returned 42 rows.\n"
                        "total_revenue: 1250000\n"
                        "currency: USD\nperiod: Q4 2025"
                    ),
                }
            ],
        }
    ],
}


def main() -> None:
    print("=== A2A Adapter Demo ===\n")

    # 1. Convert A2A agent card
    item = a2a_agent_to_selectable(A2A_AGENT_CARD)
    print(f"[1] Agent conversion: {item.id} ({item.kind})")
    print(f"    Name: {item.name}, Namespace: {item.namespace}")
    print(f"    Tags: {item.tags}")
    print(f"    Skills: {[s['name'] for s in item.metadata.get('skills', [])]}")

    # 2. Convert A2A task result
    envelope = a2a_result_to_envelope(A2A_TASK_RESULT, "DataAgent")
    print(f"\n[2] Result conversion: status={envelope.status}")
    print(f"    Summary: {envelope.summary}")
    print(f"    Facts: {envelope.facts}")
    print(f"    Provenance: {envelope.provenance}")

    # 3. Summarize the raw output
    summarizer = RuleBasedSummarizer(max_chars=200)
    summary = summarizer.summarize(envelope.summary, {})
    print(f"\n[3] Summarized: {summary}")

    # 4. Load A2A session from JSONL
    session_path = Path(__file__).parent / "data" / "a2a_session.jsonl"
    items = load_a2a_session_jsonl(session_path)
    print(f"\n[4] Loaded A2A session: {len(items)} events")
    for it in items:
        preview = it.text[:60].replace("\n", " ")
        print(f"    {it.id} ({it.kind.value}): {preview}...")

    # 5. Ingest and build context with projection
    mgr = ContextManager()
    for it in items:
        if it.kind == ItemKind.tool_result and len(it.text) > 500:
            mgr.ingest_tool_result(
                tool_call_id=it.parent_id or it.id,
                raw_output=it.text,
                tool_name="delegate_to_agent",
                firewall_threshold=500,
            )
        else:
            mgr.ingest(it)

    pack = mgr.build_sync(phase=Phase.answer, query="Q4 sales")
    print(f"\n[5] Context build: {pack.stats.included_count} items, {len(pack.prompt)} chars")

    # 6. Show prompt as projected view
    print("\n[6] Projected prompt (first 300 chars):")
    print(pack.prompt[:300])
    if len(pack.prompt) > 300:
        print("    ...")


if __name__ == "__main__":
    main()

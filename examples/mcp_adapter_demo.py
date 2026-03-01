"""MCP adapter demo.

Demonstrates the MCP (Model Context Protocol) integration:
  1. Convert MCP tool definitions to SelectableItems
  2. Convert MCP tool results to ResultEnvelopes
  3. Load a recorded MCP session from a JSONL file
  4. Ingest the session into a ContextManager
  5. Build context and observe firewall effects on large results
"""

from __future__ import annotations

import os
import sys

# Allow running from the repo root without installing.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from contextweaver.adapters.mcp import (
    load_mcp_session_jsonl,
    mcp_result_to_envelope,
    mcp_tool_to_item,
)
from contextweaver.context.manager import ContextManager
from contextweaver.types import Phase

# Path to the example MCP session data
_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_SESSION_FILE = os.path.join(_DATA_DIR, "mcp_session.jsonl")


def main() -> None:
    # ------------------------------------------------------------------ #
    # 1. Convert MCP tool definitions to SelectableItems                 #
    # ------------------------------------------------------------------ #
    print("=" * 60)
    print("MCP TOOL DEFINITIONS -> SelectableItems")
    print("=" * 60)

    mcp_tools = [
        {
            "name": "search_db",
            "description": "Search records in the database by SQL query",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            "annotations": {
                "tags": ["database", "search"],
                "sideEffects": False,
                "costHint": "low",
            },
        },
        {
            "name": "get_invoice",
            "description": "Retrieve invoice details by invoice ID",
            "inputSchema": {
                "type": "object",
                "properties": {"invoice_id": {"type": "integer"}},
                "required": ["invoice_id"],
            },
            "annotations": {
                "tags": ["billing", "read"],
                "sideEffects": False,
                "costHint": "free",
            },
        },
        {
            "name": "send_email",
            "description": "Send an email to a recipient",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["to", "subject", "body"],
            },
            "annotations": {
                "tags": ["communication", "email"],
                "sideEffects": True,
                "costHint": "low",
            },
        },
    ]

    for mcp_def in mcp_tools:
        item = mcp_tool_to_item(mcp_def)
        print(
            f"  {item.id:20s}  kind={item.kind:6s}  "
            f"side_effects={item.side_effects}  tags={item.tags}"
        )
    print()

    # ------------------------------------------------------------------ #
    # 2. Convert an MCP tool result to a ResultEnvelope                  #
    # ------------------------------------------------------------------ #
    print("=" * 60)
    print("MCP TOOL RESULT -> ResultEnvelope")
    print("=" * 60)

    mcp_result = {
        "content": [
            {"type": "text", "text": "Found 42 records matching your query."},
            {"type": "text", "text": "Top result: Invoice #1001 - Acme Corp - $4,500"},
        ],
        "isError": False,
    }

    envelope = mcp_result_to_envelope(mcp_result)
    print(f"  Status:  {envelope.status}")
    print(f"  Summary: {envelope.summary}")
    print(f"  Facts:   {envelope.facts}")
    print()

    # ------------------------------------------------------------------ #
    # 3. Load an MCP session from JSONL                                  #
    # ------------------------------------------------------------------ #
    print("=" * 60)
    print("LOAD MCP SESSION FROM JSONL")
    print("=" * 60)

    items = load_mcp_session_jsonl(_SESSION_FILE)
    print(f"  Loaded {len(items)} context items from {os.path.basename(_SESSION_FILE)}")
    for ci in items:
        parent_info = f" (parent={ci.parent_id})" if ci.parent_id else ""
        print(
            f"    {ci.id:6s}  {ci.kind.value:12s}  "
            f"tokens~{ci.token_estimate:>4d}{parent_info}  "
            f"{ci.text[:60]}..."
        )
    print()

    # ------------------------------------------------------------------ #
    # 4. Ingest into ContextManager and build context                    #
    # ------------------------------------------------------------------ #
    print("=" * 60)
    print("INGEST INTO CONTEXT MANAGER")
    print("=" * 60)

    mgr = ContextManager()
    for ci in items:
        mgr.ingest_sync(ci)

    mgr.add_fact_sync("session_source", "MCP protocol adapter")
    mgr.add_fact_sync("tool_count", "3 tools available")

    # Build context for different phases
    for phase in [Phase.ROUTE, Phase.INTERPRET, Phase.ANSWER]:
        pack = mgr.build_sync(
            goal="Process unpaid invoices from last quarter",
            phase=phase,
        )
        print(f"\n  Phase: {phase.value}")
        print(f"    Budget used / total:  {pack.budget_used} / {pack.budget_total}")
        print(f"    Included items:       {pack.stats.included_count}")
        print(f"    Dropped items:        {pack.stats.dropped_count}")
        print(f"    Tokens per section:   {pack.stats.tokens_per_section}")

    # ------------------------------------------------------------------ #
    # 5. Show firewall effect on the large tool result                   #
    # ------------------------------------------------------------------ #
    print()
    print("=" * 60)
    print("FIREWALL EFFECT")
    print("=" * 60)

    # Find the large tool result item (tr1 has the 15-invoice listing)
    large_item = next((ci for ci in items if ci.id == "tr1"), None)
    if large_item:
        print(
            f"  Original tool result size: {len(large_item.text)} chars "
            f"(~{large_item.token_estimate} tokens)"
        )

        # Re-ingest through the firewall with a low threshold
        mgr2 = ContextManager()
        _, fw_envelope = mgr2.ingest_tool_result_sync(
            tool_call_id="tc1_fw",
            raw_output=large_item.text,
            tool_name="search_db",
            firewall_threshold=500,  # low threshold to force firewall
        )
        print(f"  Firewall summary:  {fw_envelope.summary[:120]}...")
        print(f"  Extracted facts:   {fw_envelope.facts}")
        if fw_envelope.artifacts:
            print(
                f"  Stored artifact:   {fw_envelope.artifacts[0].handle} "
                f"({fw_envelope.artifacts[0].size_bytes} bytes)"
            )
        print(f"  Artifact handles:  {mgr2.artifact_store.list_refs()}")


if __name__ == "__main__":
    main()

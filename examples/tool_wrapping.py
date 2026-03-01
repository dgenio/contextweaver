"""Tool wrapping example.

Shows how to wrap a raw tool output through the context firewall using
ContextManager.ingest_tool_result_sync.  When the output exceeds the
firewall threshold the raw payload is stored in the ArtifactStore and
replaced by a concise summary in the ContextItem that the LLM sees.

Demonstrates:
  - ResultEnvelope with summary, extracted facts, and ArtifactRef
  - Handles, views, and drilldown for out-of-band artifacts
  - Structured extraction of entities from raw output
"""

from __future__ import annotations

import os
import sys

# Allow running from the repo root without installing.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from contextweaver.context.manager import ContextManager
from contextweaver.types import ContextItem, ItemKind, Phase


def _make_large_output() -> str:
    """Generate a realistic large tool output (~3 000 chars) that will
    exceed the default firewall threshold of 2 000 chars."""
    header = (
        "Query Results - Customer Database Export\n"
        "=========================================\n"
        "Status: success\n"
        "Total rows: 50\n"
        "Execution time: 245ms\n\n"
    )
    rows = []
    for i in range(1, 51):
        rows.append(
            f"Row {i:>3}: id={1000 + i}  name=Customer_{i:03d}  "
            f"email=customer{i}@example.com  "
            f"balance=${i * 123.45:.2f}  "
            f"status={'active' if i % 3 != 0 else 'inactive'}  "
            f"region={'NA' if i % 4 == 0 else 'EMEA' if i % 4 == 1 else 'APAC' if i % 4 == 2 else 'LATAM'}"
        )
    footer = (
        "\n\nSummary:\n"
        "  Active customers: 34\n"
        "  Inactive customers: 16\n"
        "  Total balance: $309,862.50\n"
        "  Average balance: $6,197.25\n"
    )
    return header + "\n".join(rows) + footer


def main() -> None:
    mgr = ContextManager()

    # Ingest the user request and tool call first
    mgr.ingest_sync(
        ContextItem(
            id="u1",
            kind=ItemKind.USER_TURN,
            text="Export all customers from the database.",
            token_estimate=8,
        )
    )
    mgr.ingest_sync(
        ContextItem(
            id="tc1",
            kind=ItemKind.TOOL_CALL,
            text='db_export(table="customers")',
            token_estimate=6,
            parent_id="u1",
        )
    )

    # ------------------------------------------------------------------ #
    # Ingest a LARGE tool result through the firewall                    #
    # ------------------------------------------------------------------ #
    large_output = _make_large_output()
    print(f"Raw output size: {len(large_output)} chars (~{len(large_output) // 4} tokens)")
    print()

    item, envelope = mgr.ingest_tool_result_sync(
        tool_call_id="tc1",
        raw_output=large_output,
        tool_name="db_export",
        media_type="text/plain",
        firewall_threshold=2000,  # output exceeds this -> firewall kicks in
    )

    # ------------------------------------------------------------------ #
    # Inspect what the LLM will actually see                             #
    # ------------------------------------------------------------------ #
    print("=" * 60)
    print("CONTEXT ITEM (what the LLM sees)")
    print("=" * 60)
    print(f"  ID:             {item.id}")
    print(f"  Kind:           {item.kind.value}")
    print(f"  Token estimate: {item.token_estimate}")
    print(f"  Artifact ref:   {item.artifact_ref}")
    print(f"  Text (summary):\n    {item.text}")
    print()

    # ------------------------------------------------------------------ #
    # Inspect the ResultEnvelope                                         #
    # ------------------------------------------------------------------ #
    print("=" * 60)
    print("RESULT ENVELOPE")
    print("=" * 60)
    print(f"  Status:  {envelope.status}")
    print(f"  Summary: {envelope.summary[:200]}")
    print(f"  Facts:   {envelope.facts}")
    if envelope.artifacts:
        art = envelope.artifacts[0]
        print(f"  Artifact handle:   {art.handle}")
        print(f"  Artifact media:    {art.media_type}")
        print(f"  Artifact size:     {art.size_bytes} bytes")
    if envelope.views:
        view = envelope.views[0]
        print(f"  View ID:    {view.view_id}")
        print(f"  View label: {view.label}")
    print()

    # ------------------------------------------------------------------ #
    # Demonstrate artifact store drilldown                               #
    # ------------------------------------------------------------------ #
    print("=" * 60)
    print("ARTIFACT STORE")
    print("=" * 60)
    refs = mgr.artifact_store.list_refs()
    print(f"  Stored handles: {refs}")
    for ref in refs:
        print(f"\n  Handle: {ref}")
        # Drilldown is async; use a quick sync call
        import asyncio

        head = asyncio.run(mgr.artifact_store.drilldown(ref, {"type": "head", "chars": 200}))
        print(f"  First 200 chars:\n    {head[:200]}")
    print()

    # ------------------------------------------------------------------ #
    # Now build context — the summarized version fits the budget          #
    # ------------------------------------------------------------------ #
    pack = mgr.build_sync(
        goal="Interpret the customer export results",
        phase=Phase.INTERPRET,
    )
    print("=" * 60)
    print("CONTEXT PACK (INTERPRET phase)")
    print("=" * 60)
    print(f"  Budget used / total: {pack.budget_used} / {pack.budget_total}")
    print(f"  Included items:      {pack.stats.included_count}")
    print(f"  Artifacts available: {pack.artifacts_available}")
    print("\n  Rendered text (first 500 chars):")
    print(f"    {pack.rendered_text[:500]}")


if __name__ == "__main__":
    main()

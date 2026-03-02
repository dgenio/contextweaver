"""MCP adapter demo.

Demonstrates converting MCP tool definitions and results into
contextweaver-native types, and ingesting an MCP session from a JSONL file
with firewall interception and drilldown.
"""

from __future__ import annotations

from pathlib import Path

from contextweaver.adapters.mcp import (
    load_mcp_session_jsonl,
    mcp_result_to_envelope,
    mcp_tool_to_selectable,
)
from contextweaver.context.manager import ContextManager
from contextweaver.types import ItemKind, Phase

MCP_TOOL_DEF = {
    "name": "search_db",
    "description": "Search records in the database",
    "inputSchema": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
    "annotations": {"readOnlyHint": True, "costHint": 0.1},
}

MCP_TOOL_RESULT = {
    "content": [
        {"type": "text", "text": "Found 42 records matching your query.\nstatus: ok\ntotal: 42"}
    ],
    "isError": False,
}


def main() -> None:
    print("=== MCP Adapter Demo ===\n")

    # 1. Convert MCP tool definition
    item = mcp_tool_to_selectable(MCP_TOOL_DEF)
    print(f"[1] Tool conversion: {item.id} ({item.kind})")
    print(f"    Name: {item.name}, Namespace: {item.namespace}")
    print(f"    Tags: {item.tags}, Side effects: {item.side_effects}")
    print(f"    Has schema: {bool(item.args_schema)}")

    # 2. Convert MCP tool result
    envelope, binaries = mcp_result_to_envelope(MCP_TOOL_RESULT, "search_db")
    print(f"\n[2] Result conversion: status={envelope.status}")
    print(f"    Summary: {envelope.summary[:100]}")
    print(f"    Facts: {envelope.facts}")
    print(f"    Provenance: {envelope.provenance}")
    print(f"    Binary artifacts: {len(binaries)}")

    # 3. Ingest MCP session from JSONL
    session_path = Path(__file__).parent / "data" / "mcp_session.jsonl"
    items = load_mcp_session_jsonl(session_path)
    print(f"\n[3] Loaded MCP session: {len(items)} events")
    for it in items:
        preview = it.text[:60].replace("\n", " ")
        print(f"    {it.id} ({it.kind.value}): {preview}...")

    # 4. Ingest into ContextManager and build with firewall
    mgr = ContextManager()
    firewall_count = 0
    for it in items:
        if it.kind == ItemKind.tool_result and len(it.text) > 500:
            _, env = mgr.ingest_tool_result(
                tool_call_id=it.parent_id or it.id,
                raw_output=it.text,
                tool_name=str(it.metadata.get("tool_name", "")),
                firewall_threshold=500,
            )
            firewall_count += 1
            print(f"\n[4] Firewall triggered for {it.id}: {env.status}")
            print(f"    Summary: {env.summary[:80]}...")
            print(f"    Facts extracted: {len(env.facts)}")
            if env.artifacts:
                print(f"    Artifact stored: {env.artifacts[0].handle}")
        else:
            mgr.ingest(it)

    # 5. Drilldown into stored artifact
    refs = mgr.artifact_store.list_refs()
    if refs:
        ref = refs[0]
        head = mgr.artifact_store.drilldown(ref.handle, {"type": "head", "chars": 120})
        print(f"\n[5] Drilldown (first 120 chars of {ref.handle}):")
        print(f"    {head}")

    # 6. Build context
    pack = mgr.build_sync(phase=Phase.answer, query="invoices")
    print(
        f"\n[6] Context build: {pack.stats.included_count} items included, "
        f"{pack.stats.dropped_count} dropped"
    )
    print(f"    Firewall triggers: {firewall_count}")
    print(f"    Prompt length: {len(pack.prompt)} chars")

    # 7. Happy-path: ingest_mcp_result (one-call artifact persistence)
    mgr2 = ContextManager()
    mcp_with_image = {
        "content": [
            {"type": "text", "text": "Screenshot captured"},
            {"type": "image", "data": "iVBORw0KGgo=", "mimeType": "image/png"},
        ],
    }
    item2, env2 = mgr2.ingest_mcp_result("call-img", mcp_with_image, "screenshot")
    print(f"\n[7] ingest_mcp_result: {item2.id}")
    print(f"    Artifacts in envelope: {len(env2.artifacts)}")
    print(f"    Artifact persisted: {mgr2.artifact_store.exists('mcp:screenshot:image:1')}")
    print(f"    Event log count: {mgr2.event_log.count()}")


if __name__ == "__main__":
    main()

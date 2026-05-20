"""MCP Context Gateway — live-transport variant (issue #260).

Sibling of :mod:`main`. Same scenario — "Why did customer C-12345's MRR
drop last month?" — but the gateway runs as a real
:class:`mcp.server.Server` and the agent walks the three meta-tools
(``tool_browse`` / ``tool_execute`` / ``tool_view``) through a real
:class:`mcp.client.session.ClientSession`.

The transport is pumped in-memory via
:func:`mcp.shared.memory.create_connected_server_and_client_session`, so
the example stays network-free, deterministic, and runnable under
``make example`` in CI — but every byte of the routing → execute →
firewall path travels over the real MCP wire protocol.

What this proves over :mod:`main`:

1. The catalog YAML + sidecar schema file (loaded by ``hydrate_with_schema``
   from issue #261) can drive a live MCP gateway with **zero** extra
   plumbing — same files, same helpers.
2. The gateway primitives (``ProxyRuntime`` + ``McpGatewayServer``) emit
   the same ChoiceCard / firewall summary / artifact handle shape that
   ``main`` shows; the MCP transport is a wrapping concern, not a
   re-implementation.

For the live-stdio variant against a subprocess, swap the in-memory
session for :func:`mcp.client.stdio.stdio_client`. The dispatch logic
below is identical.

Run standalone::

    python examples/architectures/mcp_context_gateway/main_live.py

Or via ``make architectures`` / ``make example``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from mcp import types as mcp_types
from mcp.shared.memory import create_connected_server_and_client_session

from contextweaver.adapters import ProxyRuntime, StubUpstream
from contextweaver.adapters.mcp_gateway_server import McpGatewayServer
from contextweaver.routing.catalog import load_catalog_yaml
from contextweaver.routing.hydration import SchemaSource

CATALOG_PATH = Path(__file__).parent / "catalog.yaml"
SCHEMAS_PATH = Path(__file__).parent / "tool_schemas.json"

USER_TYPED_QUERY = "Why did customer C-12345's MRR drop last month?"
ROUTING_QUERY = "Execute a BigQuery query to find MRR delta rows for customer C-12345"
SELECTED_TOOL_NAME = "bigquery.run_query"


def _mock_bigquery_result() -> dict[str, Any]:
    """Return the same canned 16 KB rowset the offline variant uses.

    Mirrors :func:`main._mock_bigquery_result` so the live and offline
    captured outputs converge on the same firewall reduction numbers.
    """
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
    """Print a section banner consistent with the offline ``main.py``."""
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def _build_upstream_tool_defs() -> list[dict[str, Any]]:
    """Project the catalog YAML + sidecar schemas into MCP tool-def dicts.

    The MCP wire shape is ``{"name": ..., "description": ..., "inputSchema": ...}``.
    The catalog YAML carries the routing-shaped fields; the sidecar JSON
    provides the full input schema for the one tool we actually execute
    in this scenario. Tools with no sidecar schema get the standard
    empty-object schema, matching the upstream contract documented in
    ``docs/gateway_spec.md`` §4.4.
    """
    schemas = SchemaSource.from_json_file(SCHEMAS_PATH)
    defs: list[dict[str, Any]] = []
    for item in load_catalog_yaml(CATALOG_PATH):
        schema = schemas.get_schema(item.id) or {"type": "object", "properties": {}}
        defs.append(
            {
                "name": item.id,
                "description": item.description,
                "inputSchema": schema,
            }
        )
    return defs


async def _stub_handler(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Canned upstream handler — only the selected tool returns a real body."""
    if name == SELECTED_TOOL_NAME:
        return _mock_bigquery_result()
    return {
        "content": [{"type": "text", "text": f"stub upstream called {name}"}],
        "isError": False,
    }


def _decode_text_content(result: mcp_types.CallToolResult) -> str:
    """Concatenate the ``text`` parts of an MCP call-tool result."""
    parts: list[str] = []
    for block in result.content:
        if isinstance(block, mcp_types.TextContent):
            parts.append(block.text)
    return "".join(parts)


async def _run() -> None:
    """Run the scenario end-to-end over an in-memory MCP transport."""
    _print_header("contextweaver -- MCP Context Gateway (LIVE transport)")
    print("(real mcp.server.Server + mcp.ClientSession over in-memory transport)")

    upstream_defs = _build_upstream_tool_defs()
    catalog_tools = len(upstream_defs)
    print(f"\nLoaded catalog: {catalog_tools} tools (MCP-shaped upstream defs)")

    runtime = ProxyRuntime(StubUpstream(upstream_defs, handler=_stub_handler))
    runtime.register_tool_defs_sync(upstream_defs)
    server = McpGatewayServer(runtime, name="contextweaver-gateway-live")

    async with create_connected_server_and_client_session(server.server) as client:
        # --------------------------------------------------------------
        # 1. tool_browse — the agent asks the gateway for a shortlist.
        # --------------------------------------------------------------
        _print_header("[1/4] tool_browse via real MCP ClientSession")
        listing = await client.list_tools()
        meta_names = sorted(t.name for t in listing.tools)
        print(f"meta-tools advertised by gateway: {meta_names}")

        browse = await client.call_tool("tool_browse", {"query": ROUTING_QUERY})
        cards_json = _decode_text_content(browse)
        cards = json.loads(cards_json)
        shortlist = [card["id"] for card in cards]
        # The gateway canonicalises ids (``namespace:name#hash8`` per
        # docs/gateway_spec.md §1) so they round-trip safely over the wire.
        # Offline ``main.py`` uses raw catalog ids; both are equivalent
        # views of the same SelectableItem.
        print(f"shortlist ({len(shortlist)} of {catalog_tools}): {shortlist[:5]} ...")
        print(f"ChoiceCards payload size: {len(cards_json)} chars (NO full schemas)")

        # --------------------------------------------------------------
        # 2. tool_execute — schema hydrated server-side, args validated.
        # --------------------------------------------------------------
        # Find the canonical tool_id whose ``name`` part matches the
        # selected upstream tool name (the gateway's id format keeps the
        # original name as the segment between ``:`` and ``#``).
        chosen = next(
            (
                cid
                for cid in shortlist
                if cid.split(":", 1)[1].split("#", 1)[0] == SELECTED_TOOL_NAME
            ),
            shortlist[0],
        )
        _print_header(f"[2/4] tool_execute({chosen}) via real MCP ClientSession")
        exec_args = {
            "sql": (
                "SELECT date, plan, mrr_delta_usd, reason_code, actor, notes "
                "FROM `ops-analytics-prod.billing.mrr_changes` "
                "WHERE customer_id = 'C-12345' "
                "AND date BETWEEN '2026-02-01' AND '2026-04-30' "
                "ORDER BY date"
            ),
            "max_results": 1000,
        }
        execute = await client.call_tool(
            "tool_execute",
            {"tool_id": chosen, "args": exec_args},
        )
        envelope_text = _decode_text_content(execute)
        envelope = json.loads(envelope_text)
        print(f"envelope status:   {envelope['status']}")
        print(f"envelope summary:  {len(envelope['summary'])} chars")
        # The envelope's ``artifacts`` list carries upstream-supplied
        # artifact refs (the inline ``ResultEnvelope.artifacts`` field).
        # The gateway also persists text bodies above the firewall threshold
        # to the runtime's artifact store under a deterministic
        # ``text:<tool_id>:<sha16>`` handle so ``tool_view`` can drill in.
        # We surface that handle here for parity with the offline ``main.py``
        # which prints an explicit artifact reference.
        envelope_artifacts = envelope.get("artifacts", []) or []
        store_refs = list(runtime.context_manager.artifact_store.list_refs())
        if envelope_artifacts:
            artifact_handle = envelope_artifacts[0]["handle"]
        elif store_refs:
            artifact_handle = store_refs[0].handle
        else:
            artifact_handle = "<none>"
        print(f"artifact handle:   {artifact_handle}")
        if envelope.get("facts"):
            facts = envelope["facts"]
            print(f"extracted facts (first 3 of {len(facts)}):")
            for fact in facts[:3]:
                print(f"  - {fact}")

        # --------------------------------------------------------------
        # 3. tool_view — drill into the artifact via the meta-tool.
        # --------------------------------------------------------------
        _print_header("[3/4] tool_view(artifact) via real MCP ClientSession")
        if artifact_handle != "<none>":
            view = await client.call_tool(
                "tool_view",
                {"handle": artifact_handle, "selector": {"type": "head", "n_chars": 80}},
            )
            head = _decode_text_content(view)
            print(f"head view (80 chars): {head[:80]!r}")
        else:
            print("(no artifact persisted — text-only upstream response)")

        # --------------------------------------------------------------
        # 4. Metrics summary — same shape as the offline variant.
        # --------------------------------------------------------------
        _print_header("[4/4] Metrics summary (matches offline main.py)")
        raw_text = _mock_bigquery_result()["content"][0]["text"]
        raw_chars = len(raw_text)
        summary_chars = len(envelope["summary"])
        saving_pct = 100.0 * (1.0 - summary_chars / max(raw_chars, 1))
        print(f"catalog_tools           = {catalog_tools}")
        print(f"exposed_choice_cards    = {len(shortlist)}")
        print(f"raw_result_chars        = {raw_chars:,}")
        print(f"injected_summary_chars  = {summary_chars:,}")
        print(f"firewall_reduction_pct  = {saving_pct:.1f}%")
        print(f"artifact_handle         = {artifact_handle}")
        print("transport               = mcp.shared.memory (in-process)")


def main() -> None:
    """Sync entrypoint for ``make architectures`` parity with ``main.py``."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()

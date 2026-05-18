"""Real FastMCP integration tests for the CodeMode hooks (issue #87).

These tests spin up an in-memory ``fastmcp.FastMCP`` server, load its
catalog via :func:`~contextweaver.adapters.fastmcp.load_fastmcp_catalog`,
wrap the resulting router with
:func:`~contextweaver.adapters.fastmcp.make_discovery_tool`, and assert that
the discovery hook returns shortlists whose dict shapes are compatible with
FastMCP's wire format.

The unit-level tests in ``test_adapters.py`` cover the callable contract.
This file is intentionally narrow: it just proves the hooks line up with a
real FastMCP runtime, so that a downstream adopter who follows the example
in ``examples/fastmcp_discovery_demo.py`` does not hit a wire-format
mismatch on their first call.
"""

from __future__ import annotations

import pytest

# Module-level importorskip: skip this whole file when ``fastmcp`` is not
# importable (a minimal-runtime install where neither ``[dev]`` nor
# ``[fastmcp]`` was pulled).  ``fastmcp>=2.0`` is part of the ``[dev]``
# extra so every CI matrix cell runs this file; this guard only protects
# users running tests against a stripped-down install.  The unit tests in
# test_adapters.py still run, so coverage of the adapter does not regress.
fastmcp = pytest.importorskip("fastmcp")

from contextweaver.adapters.fastmcp import (  # noqa: E402  — import-after-skip is intentional
    load_fastmcp_catalog,
    make_context_hook,
    make_discovery_tool,
)
from contextweaver.context.manager import ContextManager  # noqa: E402
from contextweaver.routing.router import Router  # noqa: E402
from contextweaver.routing.tree import TreeBuilder  # noqa: E402


@pytest.fixture
def fastmcp_server() -> object:
    """A small in-memory FastMCP server with three distinct-domain tools."""
    server = fastmcp.FastMCP(name="contextweaver-test-server")

    @server.tool
    def github_search_repos(query: str) -> list[str]:
        """Search GitHub repositories by keyword."""
        return [f"repo:{query}"]

    @server.tool
    def slack_send_message(channel: str, text: str) -> str:
        """Post a message to a Slack channel."""
        return f"sent to {channel}: {text}"

    @server.tool
    def db_query(sql: str) -> list[dict[str, str]]:
        """Run a read-only SQL query against the warehouse."""
        return [{"sql": sql, "rows": "0"}]

    return server


async def test_load_catalog_and_wrap_as_discovery_tool(fastmcp_server: object) -> None:
    """End-to-end: real FastMCP server -> catalog -> discovery callable -> shortlist."""
    catalog = await load_fastmcp_catalog(fastmcp_server)
    items = catalog.all()
    # Three tools registered above.
    assert len(items) == 3

    graph = TreeBuilder(max_children=8).build(items)
    router = Router(graph, items=items, top_k=3)
    discover = make_discovery_tool(router, catalog)

    # The discovery callable is shape-compatible with FastMCP CodeMode's
    # custom_discovery_tool contract: list of dicts, each with name +
    # description + input_schema.
    out = discover("search github repositories for a project")
    assert len(out) >= 1
    for tool in out:
        assert {"name", "description", "input_schema"} <= set(tool.keys())
        assert isinstance(tool["input_schema"], dict)

    names = [t["name"] for t in out]
    # The relevant tool surfaces in the shortlist.  Exact ordering depends
    # on TF-IDF scoring against descriptions, so only assert membership.
    # FastMCP-derived ``SelectableItem.name`` has the namespace prefix
    # stripped (``github_search_repos`` -> ``search_repos``).
    assert "search_repos" in names


async def test_discovery_tool_input_schema_matches_fastmcp_wire_shape(
    fastmcp_server: object,
) -> None:
    """The schemas the hook returns must match what FastMCP's own list_tools yields."""
    catalog = await load_fastmcp_catalog(fastmcp_server)
    items = catalog.all()
    graph = TreeBuilder(max_children=8).build(items)
    router = Router(graph, items=items, top_k=3)
    discover = make_discovery_tool(router, catalog)

    # Cross-check one schema against the catalog item directly (the adapter's
    # round-trip is what we're really validating here).  Note: the FastMCP
    # adapter strips the namespace prefix from the tool name, so the python
    # function ``github_search_repos`` surfaces as ``search_repos``.
    out = discover("search github repositories")
    matched = next((t for t in out if t["name"] == "search_repos"), None)
    assert matched is not None, f"expected search_repos in shortlist; got {out}"

    # Schema must declare a 'query' parameter — matches the python signature.
    schema = matched["input_schema"]
    assert schema.get("type") == "object"
    properties = schema.get("properties", {})
    assert "query" in properties


async def test_context_hook_compacts_real_fastmcp_tool_call() -> None:
    """End-to-end: invoke a FastMCP tool, feed its result to make_context_hook, verify firewall."""
    # Spin up a tiny in-memory server with a tool that returns a chunky payload.
    server = fastmcp.FastMCP(name="contextweaver-context-hook-test-server")

    @server.tool
    def fetch_logs(service: str) -> str:
        """Return a synthetic large log payload."""
        # ~5 KB — well above the firewall_threshold below.
        return "\n".join(
            f'service={service} ts=2026-05-18T06:{i:02d}:00Z level=INFO msg="event {i}"'
            for i in range(80)
        )

    # Drive the tool through fastmcp's in-memory client to make sure the wire
    # format round-trips through the real FastMCP runtime, not just our adapter.
    async with fastmcp.Client(server) as client:
        result = await client.call_tool("fetch_logs", {"service": "payments"})
        # FastMCP wraps tool outputs in a `content` list of typed blocks.  We
        # only care about the textual payload here.
        raw_result = "\n".join(
            block.text for block in result.content if getattr(block, "text", None) is not None
        )

    # raw_result is the actual on-wire string the LLM would otherwise see.
    assert len(raw_result) > 2000  # confirm we're above the firewall threshold

    mgr = ContextManager()
    hook = make_context_hook(mgr, firewall_threshold=2000, tool_name="logs.fetch_logs")
    summary = hook("what failed in payments?", raw_result)

    # Firewall fires: prompt-side summary is bounded, raw bytes parked.
    assert len(summary) < len(raw_result)
    assert len(list(mgr.artifact_store.list_refs())) == 1

    # The configured tool_name and the query metadata both reach the event log
    # via the ContextItem metadata bag (post-audit-fix M-1, where `tool_name`
    # became a hook parameter and stops being hardcoded).
    events = mgr.event_log.all()
    tool_result = events[-1]
    assert tool_result.metadata.get("tool_name") == "logs.fetch_logs"
    assert tool_result.metadata.get("codemode_query") == "what failed in payments?"

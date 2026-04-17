"""FastMCP adapter demo.

Demonstrates converting FastMCP tool definitions into contextweaver-native
types and building a Catalog from them.  All conversions use plain dicts —
no ``fastmcp`` install required for this demo.

For live server discovery (``load_fastmcp_catalog``), install the optional
extra: ``pip install 'contextweaver[fastmcp]'``
"""

from __future__ import annotations

from contextweaver.adapters.fastmcp import (
    fastmcp_tool_to_selectable,
    fastmcp_tools_to_catalog,
    infer_fastmcp_namespace,
)

# Simulated tools as they would appear from a FastMCP composed server
# with namespace-prefixed names (https://gofastmcp.com/servers/composition).
FASTMCP_TOOLS: list[dict[str, object]] = [
    {
        "name": "github_search_repos",
        "description": "Search GitHub repositories by keyword",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["query"],
        },
        "annotations": {"readOnlyHint": True, "costHint": 0.1},
        "meta": {"tags": ["search", "vcs"], "version": "1.0"},
    },
    {
        "name": "github_create_issue",
        "description": "Create a new issue in a GitHub repository",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["repo", "title"],
        },
        "annotations": {"destructiveHint": False},
        "meta": {"tags": ["vcs"]},
    },
    {
        "name": "slack_send_message",
        "description": "Send a message to a Slack channel",
        "inputSchema": {
            "type": "object",
            "properties": {"channel": {"type": "string"}, "text": {"type": "string"}},
            "required": ["channel", "text"],
        },
        "meta": {"tags": ["messaging"]},
    },
    {
        "name": "db_query",
        "description": "Run a read-only SQL query",
        "inputSchema": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
        "annotations": {"readOnlyHint": True},
    },
]


def main() -> None:
    print("=== FastMCP Adapter Demo ===\n")

    # 1. Namespace inference
    print("[1] Namespace inference:")
    for name in ["github_search_repos", "slack_send_message", "db_query", "search"]:
        ns = infer_fastmcp_namespace(name)
        print(f"    {name!r:30s} → namespace={ns!r}")

    # 2. Single tool conversion
    print("\n[2] Single tool conversion:")
    item = fastmcp_tool_to_selectable(FASTMCP_TOOLS[0])  # type: ignore[arg-type]
    print(f"    ID:          {item.id}")
    print(f"    Name:        {item.name}")
    print(f"    Namespace:   {item.namespace}")
    print(f"    Tags:        {item.tags}")
    print(f"    Side effects:{item.side_effects}")
    print(f"    Cost hint:   {item.cost_hint}")
    print(f"    Has schema:  {bool(item.args_schema)}")

    # 3. Batch conversion → Catalog
    print("\n[3] Building Catalog from 4 FastMCP tools:")
    catalog = fastmcp_tools_to_catalog(FASTMCP_TOOLS)  # type: ignore[arg-type]
    for it in catalog.all():
        print(f"    {it.id:40s} ns={it.namespace:10s} name={it.name}")

    # 4. Namespace-scoped queries
    print("\n[4] Namespace filter (github):")
    for it in catalog.filter_by_namespace("github"):
        print(f"    {it.id}: {it.description}")

    # 5. Tag-based filter
    print("\n[5] Tag filter (vcs):")
    for it in catalog.filter_by_tags("vcs"):
        print(f"    {it.id}: {it.tags}")

    # 6. Hydration
    print("\n[6] Hydrate a tool:")
    hydration = catalog.hydrate("fastmcp:github_search_repos")
    print(f"    Item:        {hydration.item.name}")
    print(f"    Schema keys: {list(hydration.args_schema.get('properties', {}).keys())}")

    print("\nDone.")


if __name__ == "__main__":
    main()

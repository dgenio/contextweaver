"""FastMCP CodeMode custom-discovery-tool demo (issue #87).

Wraps contextweaver's :class:`~contextweaver.routing.router.Router` as a plain
callable suitable for FastMCP CodeMode's custom discovery hook, then
demonstrates how to use it to shrink a 22-tool catalog down to a 3-tool
shortlist that the downstream LLM actually sees.

The two helpers used here —
:func:`~contextweaver.adapters.fastmcp.make_discovery_tool` and
:func:`~contextweaver.adapters.fastmcp.make_context_hook` — return pure
callables.  Neither imports ``fastmcp`` at runtime, so this demo works on a
stock ``pip install contextweaver`` install.  The same callables can be
handed to a real FastMCP server's ``custom_discovery_tool`` parameter; the
adapter is the bridge, not the runtime.

References:
- FastMCP CodeMode discussion: https://github.com/PrefectHQ/fastmcp/discussions/3365
- Issue: https://github.com/dgenio/contextweaver/issues/87

Run standalone::

    python examples/fastmcp_discovery_demo.py
"""

from __future__ import annotations

from contextweaver.adapters.fastmcp import make_context_hook, make_discovery_tool
from contextweaver.context.manager import ContextManager
from contextweaver.routing.cards import count_tokens
from contextweaver.routing.catalog import Catalog
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import SelectableItem

# 22 tools across 5 namespaces — realistic mid-size catalog.  Same shape as
# what a FastMCP-composed server would expose after pulling in a couple of
# third-party MCP servers (GitHub, Slack, Linear, Postgres, search).
_TOOLS: list[tuple[str, str, str, list[str]]] = [
    # (id, name, description, tags)
    (
        "github.search_repos",
        "search_repos",
        "Search GitHub repositories by keyword",
        ["search", "vcs"],
    ),
    (
        "github.create_issue",
        "create_issue",
        "Create a new GitHub issue in a repository",
        ["vcs", "write"],
    ),
    (
        "github.get_pull_request",
        "get_pull_request",
        "Fetch a GitHub pull request by number",
        ["vcs", "pull request"],
    ),
    ("github.list_commits", "list_commits", "List git commits on a branch", ["vcs"]),
    (
        "github.merge_pull_request",
        "merge_pull_request",
        "Merge an approved GitHub pull request",
        ["vcs", "pull request", "write"],
    ),
    (
        "slack.send_channel_message",
        "send_channel_message",
        "Post a message update to a Slack channel",
        ["messaging", "channel", "write"],
    ),
    (
        "slack.search_messages",
        "search_messages",
        "Search Slack channel messages by query",
        ["search", "messaging"],
    ),
    ("slack.list_channels", "list_channels", "List Slack channels the bot can see", ["messaging"]),
    ("slack.set_topic", "set_topic", "Set the topic of a Slack channel", ["messaging", "write"]),
    (
        "linear.create_ticket",
        "create_ticket",
        "Open a new Linear ticket for an incident or task",
        ["tickets", "incident", "write"],
    ),
    (
        "linear.update_ticket",
        "update_ticket",
        "Update an existing Linear ticket",
        ["tickets", "write"],
    ),
    (
        "linear.search_tickets",
        "search_tickets",
        "Search Linear tickets by keyword",
        ["search", "tickets"],
    ),
    ("linear.close_ticket", "close_ticket", "Close a resolved Linear ticket", ["tickets", "write"]),
    (
        "db.query",
        "db_query",
        "Run a read-only SQL query against the data warehouse",
        ["database", "sql"],
    ),
    ("db.explain", "db_explain", "Show the query plan for a SQL statement", ["database", "sql"]),
    ("db.list_tables", "db_list_tables", "List warehouse tables and views", ["database"]),
    (
        "db.row_count",
        "db_row_count",
        "Get an approximate row count for a warehouse table",
        ["database"],
    ),
    ("search.web", "web_search", "Search the public web", ["search", "web"]),
    (
        "search.docs",
        "docs_search",
        "Search internal engineering documentation pages",
        ["search", "docs", "documentation"],
    ),
    ("search.code", "code_search", "Search the codebase by symbol or text", ["search", "code"]),
    (
        "search.tickets_global",
        "tickets_global",
        "Search incident tickets across Linear and GitHub",
        ["search", "tickets", "incident"],
    ),
    ("search.everything", "search_everything", "Federated search across all backends", ["search"]),
]


def _build_catalog() -> Catalog:
    """Build a 22-item catalog with consistent metadata."""
    catalog = Catalog()
    for tid, name, desc, tags in _TOOLS:
        namespace = tid.split(".", 1)[0]
        catalog.register(
            SelectableItem(
                id=tid,
                kind="tool",
                name=name,
                description=desc,
                namespace=namespace,
                tags=tags,
                side_effects="write" in tags,
                # Stable args_schema so the discovery hook returns deterministic shapes.
                args_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            )
        )
    return catalog


def _approx_tokens_for_full_catalog(catalog: Catalog) -> int:
    """Approximate token cost of dumping the whole catalog into the LLM prompt.

    Uses the same token estimator the library uses (tiktoken) so the
    before / after numbers are an apples-to-apples comparison rather than
    a hand-waved average.
    """
    total = 0
    for item in catalog.all():
        # Approximate the schema-with-description payload the LLM would see.
        blob = f"{item.name}: {item.description}\nschema: {item.args_schema}"
        total += count_tokens(blob)
    return total


def _approx_tokens_for_shortlist(shortlist: list[dict[str, object]]) -> int:
    total = 0
    for tool in shortlist:
        blob = f"{tool['name']}: {tool['description']}\nschema: {tool['input_schema']}"
        total += count_tokens(blob)
    return total


def main() -> None:
    """Run the discovery-hook demo end-to-end."""
    print("=" * 72)
    print("contextweaver -- FastMCP CodeMode discovery-tool demo (issue #87)")
    print("=" * 72)

    # 1. Build the catalog and the routing graph.
    catalog = _build_catalog()
    items = catalog.all()
    graph = TreeBuilder(max_children=8).build(items)
    router = Router(graph, items=items, top_k=3)
    print(f"\nCatalog: {len(items)} tools across {len({i.namespace for i in items})} namespaces")

    # 2. Wrap the router as a plain Callable[[str], list[dict]].
    discover = make_discovery_tool(router, catalog)
    print("Wrapped router as a plain Callable[[str], list[dict]].")
    print("No fastmcp import, no captured runtime references.")

    # 3. A handful of representative queries.  Each shows the catalog -> shortlist
    #    compression that contextweaver buys you.
    queries = [
        "find pull requests touching the payments service",
        "open a ticket for the api-gateway outage",
        "search documentation about retry semantics",
        "send a slack channel message about the rollback",
    ]

    full_catalog_tokens = _approx_tokens_for_full_catalog(catalog)
    print(f"\nFull-catalog prompt size: ~{full_catalog_tokens} tokens")

    total_shortlist_tokens = 0
    for q in queries:
        print()
        print(f"  query: {q!r}")
        shortlist = discover(q)
        names = [t["name"] for t in shortlist]
        print(f"  shortlist ({len(shortlist)}): {names}")
        toks = _approx_tokens_for_shortlist(shortlist)
        total_shortlist_tokens += toks
        print(f"  prompt size: ~{toks} tokens (saved ~{full_catalog_tokens - toks})")

    # 4. The context hook — wraps the firewall as a (query, raw_result) -> str
    #    callable for the same CodeMode contract.
    print()
    print("-" * 72)
    print("Context hook: firewall a 4 KB tool result down to a single summary line.")
    print("-" * 72)
    mgr = ContextManager()
    hook = make_context_hook(mgr)
    large_raw = (
        '{"events": ['
        + ", ".join(
            f'{{"id": {i}, "msg": "upstream timeout against payments-svc after {120 + i}ms"}}'
            for i in range(80)
        )
        + "]}"
    )
    summary = hook("what is failing in payments?", large_raw)
    print(f"  raw bytes:      {len(large_raw):,}")
    print(f"  summary chars:  {len(summary):,}")
    print(f"  artifacts kept: {len(list(mgr.artifact_store.list_refs()))}")
    print(f"  preview:        {summary[:160]!r}")

    # 5. Roll-up.
    print()
    print("=" * 72)
    print("Summary")
    print("=" * 72)
    avg_shortlist = total_shortlist_tokens // len(queries)
    saved_pct = (full_catalog_tokens - avg_shortlist) * 100 // max(full_catalog_tokens, 1)
    print(
        f"Catalog: {len(items)} tools (~{full_catalog_tokens} tokens) -> "
        f"LLM sees ~{avg_shortlist} tokens per turn ({saved_pct}% saved)."
    )
    print(
        "Plug discover() / hook() into FastMCP CodeMode (or any runtime that "
        "accepts a discovery-tool / context-hook callable) to get the same "
        "shrinkage without rewriting your agent loop."
    )


if __name__ == "__main__":
    main()

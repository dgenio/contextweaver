"""CrewAI adapter demo (issue #193).

Demonstrates converting CrewAI tool definitions into contextweaver-native
types and routing a query against the resulting catalog.  Uses plain dicts
matching the ``crewai.tools.BaseTool`` field shape — no ``crewai`` install
required for this demo.

For live conversion of real :class:`crewai.tools.BaseTool` instances,
install the optional extra: ``pip install 'contextweaver[crewai]'`` and
call :func:`contextweaver.adapters.crewai.load_crewai_catalog`.
"""

from __future__ import annotations

from contextweaver.adapters.crewai import (
    crewai_tool_to_selectable,
    crewai_tools_to_catalog,
    infer_crewai_namespace,
)
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder

# Simulated tools as they would appear from a multi-agent CrewAI deployment
# (https://docs.crewai.com/concepts/tools).  Names use the underscore-
# prefixed namespace convention used by most CrewAI catalogs.
CREWAI_TOOLS: list[dict[str, object]] = [
    {
        "name": "github_search_repos",
        "description": "Search GitHub repositories by keyword.",
        "args_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["query"],
        },
        "tags": ["vcs", "search"],
    },
    {
        "name": "github_create_issue",
        "description": "Open a new issue in a GitHub repository.",
        "args_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["repo", "title"],
        },
        "tags": ["vcs"],
        "result_as_answer": False,
    },
    {
        "name": "slack_send_message",
        "description": "Send a Slack message to a channel.",
        "args_schema": {
            "type": "object",
            "properties": {"channel": {"type": "string"}, "text": {"type": "string"}},
            "required": ["channel", "text"],
        },
        "tags": ["messaging"],
    },
    {
        "name": "calendar.create_event",
        "description": "Schedule a calendar event for a participant set.",
        "tags": ["scheduling", "write"],
    },
]


def main() -> None:
    print("=== CrewAI Adapter Demo ===\n")

    # 1. Namespace inference picks the prefix before the first ``_`` / ``.``.
    print("[1] Namespace inference:")
    for name in ("github_search_repos", "slack_send_message", "calendar.create_event"):
        print(f"    {name!r:30s} -> namespace={infer_crewai_namespace(name)!r}")

    # 2. Single conversion.
    print("\n[2] Single tool conversion:")
    item = crewai_tool_to_selectable(CREWAI_TOOLS[0])  # type: ignore[arg-type]
    print(f"    ID:        {item.id}")
    print(f"    Name:      {item.name}")
    print(f"    Namespace: {item.namespace}")
    print(f"    Tags:      {item.tags}")

    # 3. Batch conversion → Catalog.
    print("\n[3] Building Catalog from 4 CrewAI tools:")
    catalog = crewai_tools_to_catalog(CREWAI_TOOLS)  # type: ignore[arg-type]
    for it in catalog.all():
        print(f"    {it.id:40s} ns={it.namespace:10s} tags={sorted(it.tags)}")

    # 4. Routing — contextweaver narrows the catalog to a shortlist that
    # the calling LLM can reason about under a tight token budget.
    print("\n[4] Routing the query 'find typescript repos and open an issue':")
    graph = TreeBuilder(max_children=8).build(catalog.all())
    router = Router(graph, items=catalog.all(), top_k=3)
    result = router.route("find typescript repos and open an issue")
    for rank, (item, score) in enumerate(
        zip(result.candidate_items, result.scores, strict=False), 1
    ):
        print(f"    #{rank} {item.id:40s} score={score:.3f}")

    print("\nDone.")


if __name__ == "__main__":
    main()

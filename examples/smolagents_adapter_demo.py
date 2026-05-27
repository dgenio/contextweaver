"""smolagents adapter demo (issue #274).

Demonstrates the two contextweaver entry points for a smolagents agent:

1. Routing — convert smolagents ``Tool`` definitions (in their plain-dict
   form) into a contextweaver :class:`~contextweaver.routing.catalog.Catalog`,
   build a routing graph, and score a query against it to get a bounded
   shortlist.

2. Step ingestion — convert a ``MultiStepAgent``'s recorded step log into
   :class:`~contextweaver.types.ContextItem`s so the executed tool calls,
   observations, and final answer flow through the contextweaver pipeline.

Uses plain dicts matching ``smolagents.Tool`` attribute access and the
``ActionStep`` / ``TaskStep`` / ``FinalAnswerStep`` shapes that
``Tool.to_dict()`` / ``MultiStepAgent.memory.steps`` emit.  No
``smolagents`` install required for this demo.  For live conversion of real
``smolagents.Tool`` instances, install ``contextweaver[smolagents]`` and call
:func:`contextweaver.adapters.smolagents.load_smolagents_catalog`.
"""

from __future__ import annotations

from contextweaver.adapters.smolagents import (
    from_smolagents_agent,
    infer_smolagents_namespace,
    smolagents_tool_to_selectable,
    smolagents_tools_to_catalog,
)
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder

# Simulated smolagents tool definitions — the dict shape exposes
# ``inputs`` (a mapping of ``{arg: {"type": ..., "description": ..., "nullable": ?}}``)
# and ``output_type`` (string).  See ``smolagents.Tool`` for the full shape.
SMOLAGENTS_TOOLS: list[dict[str, object]] = [
    {
        "name": "web_search",
        "description": "Search the public web for a query and return top results.",
        "inputs": {
            "query": {"type": "string", "description": "User search query."},
            "top_k": {"type": "integer", "description": "Max results.", "nullable": True},
        },
        "output_type": "string",
    },
    {
        "name": "web_fetch",
        "description": "Fetch a single URL and return the raw HTML contents.",
        "inputs": {
            "url": {"type": "string", "description": "The URL to fetch."},
        },
        "output_type": "string",
    },
    {
        "name": "image_generator",
        "description": "Generate an image from a text description.",
        "inputs": {
            "prompt": {"type": "string", "description": "Image description."},
        },
        "output_type": "image",
    },
    {
        "name": "final_answer",
        "description": "Submit the agent's final answer.",
        "inputs": {
            "answer": {"type": "string", "description": "Final response to the user."},
        },
        "output_type": "string",
    },
]

# A representative smolagents ``MultiStepAgent.memory.steps`` log.  Step
# dicts follow the ``TaskStep`` / ``ActionStep`` / ``FinalAnswerStep`` shape.
SMOLAGENTS_STEPS: list[dict[str, object]] = [
    {
        "step_type": "task",
        "task": "Find the latest release of Python and report its version.",
    },
    {
        "step_type": "action",
        "model_output": "I'll search for the latest Python release.",
        "tool_calls": [
            {
                "id": "call-1",
                "name": "web_search",
                "arguments": {"query": "latest Python release 2026"},
            },
        ],
        "observations": "Python 3.13 was released on 2024-10-07.",
    },
    {
        "step_type": "action",
        "tool_calls": [
            {
                "id": "call-2",
                "name": "web_fetch",
                "arguments": {"url": "https://www.python.org/downloads/"},
            },
        ],
        "observations": "Downloads page lists 3.13.0 as current.",
    },
    {
        "step_type": "final_answer",
        "final_answer": "Python 3.13.0 is the latest release.",
    },
]


def main() -> None:
    print("=== smolagents Adapter Demo ===\n")

    # 1. Namespace inference.
    print("[1] Namespace inference:")
    for name in ("web_search", "image_generator", "final_answer"):
        print(f"    {name!r:25s} -> namespace={infer_smolagents_namespace(name)!r}")

    # 2. Single conversion.
    print("\n[2] Single tool conversion:")
    item = smolagents_tool_to_selectable(SMOLAGENTS_TOOLS[0])  # type: ignore[arg-type]
    print(f"    ID:         {item.id}")
    print(f"    Name:       {item.name}")
    print(f"    Namespace:  {item.namespace}")
    print(f"    Required:   {item.args_schema.get('required', [])}")
    print(f"    Output:     {item.metadata.get('output_type')}")

    # 3. Batch conversion → Catalog.
    print("\n[3] Building Catalog from 4 smolagents tools:")
    catalog = smolagents_tools_to_catalog(SMOLAGENTS_TOOLS)  # type: ignore[arg-type]
    for it in catalog.all():
        print(f"    {it.id:35s} ns={it.namespace:8s} tags={sorted(it.tags)}")

    # 4. Routing — narrow to a top-2 shortlist.
    print("\n[4] Routing the query 'find the latest python release':")
    graph = TreeBuilder(max_children=8).build(catalog.all())
    router = Router(graph, items=catalog.all(), top_k=2)
    result = router.route("find the latest python release")
    for rank, (sel, score) in enumerate(
        zip(result.candidate_items, result.scores, strict=False), 1
    ):
        print(f"    #{rank} {sel.id:35s} score={score:.3f}")

    # 5. Step-log ingestion.
    print("\n[5] Step-log ingestion:")
    items = from_smolagents_agent(SMOLAGENTS_STEPS)  # type: ignore[arg-type]
    print(f"    {len(items)} ContextItem(s) produced:")
    for ci in items:
        suffix = f" parent={ci.parent_id}" if ci.parent_id else ""
        text_preview = ci.text[:40] + "..." if len(ci.text) > 40 else ci.text
        print(f"      {ci.kind.value:13s} id={ci.id:35s} {text_preview!r}{suffix}")

    print("\nDone.")


if __name__ == "__main__":
    main()

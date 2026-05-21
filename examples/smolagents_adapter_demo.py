"""smolagents adapter demo (issue #274).

Demonstrates converting smolagents tool definitions into contextweaver
``SelectableItem``\\s, routing a query against the resulting catalog,
and ingesting a simulated ``MultiStepAgent.memory.steps`` log.  Uses
plain dicts matching the ``smolagents.tools.Tool`` attribute shape — no
``smolagents`` install required for this demo.

For live conversion of real :class:`smolagents.tools.Tool` instances,
install the optional extra: ``pip install 'contextweaver[smolagents]'``
and call :func:`contextweaver.adapters.smolagents.load_smolagents_catalog`.
"""

from __future__ import annotations

from contextweaver.adapters.smolagents import (
    infer_smolagents_namespace,
    smolagents_tool_to_selectable,
    smolagents_tools_to_catalog,
)
from contextweaver.adapters.smolagents_steps import from_smolagents_agent
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder

# Simulated tools as they would appear from a smolagents agent's
# ``tools`` list (https://huggingface.co/docs/smolagents/main/en/tutorials/tools).
SMOLAGENTS_TOOLS: list[dict[str, object]] = [
    {
        "name": "web_search",
        "description": "Run a DuckDuckGo web search and return the top matches.",
        "inputs": {
            "query": {"type": "string", "description": "The search query string."},
        },
        "output_type": "string",
        "tags": ["search"],
    },
    {
        "name": "wikipedia_lookup",
        "description": "Fetch the lead paragraph of a Wikipedia article.",
        "inputs": {
            "topic": {"type": "string", "description": "Article title."},
            "lang": {
                "type": "string",
                "description": "Language code.",
                "nullable": True,
            },
        },
        "output_type": "string",
        "tags": ["search", "reference"],
    },
    {
        "name": "code_interpreter",
        "description": "Execute a short Python snippet and return stdout.",
        "inputs": {
            "code": {"type": "string", "description": "Python source to evaluate."},
        },
        "output_type": "string",
        "tags": ["code"],
    },
    {
        "name": "image_classification",
        "description": "Classify the contents of an input image into ImageNet labels.",
        "inputs": {
            "image": {"type": "image", "description": "Input image."},
        },
        "output_type": "string",
        "tags": ["vision"],
    },
]

# Simulated ``MultiStepAgent.memory.steps`` after a 3-step run.
SMOLAGENTS_STEPS: list[dict[str, object]] = [
    {"task": "Summarise the Wikipedia page on type theory in two sentences."},
    {
        "model_output": "I'll look up the Wikipedia article first.",
        "tool_calls": [
            {
                "id": "call_001",
                "name": "wikipedia_lookup",
                "arguments": {"topic": "Type theory"},
            }
        ],
        "observations": "Type theory is the academic study of type systems...",
    },
    {
        "final_answer": (
            "Type theory studies type systems and serves as a foundation for "
            "many programming languages and proof assistants."
        ),
    },
]


def main() -> None:
    print("=== smolagents Adapter Demo ===\n")

    print("[1] Namespace inference:")
    for name in ("web_search", "wikipedia_lookup", "code_interpreter"):
        print(f"    {name!r:25s} → namespace={infer_smolagents_namespace(name)!r}")

    print("\n[2] Single tool conversion:")
    item = smolagents_tool_to_selectable(SMOLAGENTS_TOOLS[1])  # type: ignore[arg-type]
    print(f"    ID:           {item.id}")
    print(f"    Name:         {item.name}")
    print(f"    Namespace:    {item.namespace}")
    print(f"    Tags:         {item.tags}")
    print(f"    Output type:  {item.metadata.get('output_type')!r}")
    print(f"    Required:     {item.args_schema.get('required')}")

    print("\n[3] Building Catalog from 4 smolagents tools:")
    catalog = smolagents_tools_to_catalog(SMOLAGENTS_TOOLS)  # type: ignore[arg-type]
    for it in catalog.all():
        print(f"    {it.id:35s} ns={it.namespace:12s} tags={sorted(it.tags)}")

    print("\n[4] Routing the query 'summarize the wikipedia article on type theory':")
    graph = TreeBuilder(max_children=8).build(catalog.all())
    router = Router(graph, items=catalog.all(), top_k=3)
    result = router.route("summarize the wikipedia article on type theory")
    for rank, (it, score) in enumerate(zip(result.candidate_items, result.scores, strict=False), 1):
        print(f"    #{rank} {it.id:35s} score={score:.3f}")

    print("\n[5] Ingesting a 3-step MultiStepAgent run into ContextItems:")
    items = from_smolagents_agent(SMOLAGENTS_STEPS)
    print(f"    decoded items: {len(items)}")
    for ci in items:
        print(
            f"    - {ci.id:45s} kind={ci.kind.value:12s} step_kind={ci.metadata.get('step_kind')!r}"
        )

    print("\nDone.")


if __name__ == "__main__":
    main()

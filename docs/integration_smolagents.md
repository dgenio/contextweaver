# smolagents Integration

> Pair contextweaver's bounded-choice routing and context firewall with
> [smolagents](https://github.com/huggingface/smolagents) so a HuggingFace
> `MultiStepAgent` sees a focused shortlist of tools instead of every
> `Tool.description` and so large observations never blow up the prompt
> budget.

## Why

A smolagents `CodeAgent` / `ToolCallingAgent` running with a large
`tools=[...]` list hits two problems contextweaver was built for:

- **Tool overload.** Every `Tool` description is rendered into the
  system prompt on every step. A 20-tool agent burns 2-3 K tokens
  before any reasoning.
- **Unbounded step memory.** `MultiStepAgent.memory.steps` grows
  monotonically; one large observation poisons every subsequent step's
  prompt.

contextweaver fixes both without forking smolagents. The adapter is a
thin stateless converter (`adapters/`); no smolagents internals are
wrapped.

## Prerequisites

```bash
pip install 'contextweaver[smolagents]'
export HF_TOKEN=hf_...  # smolagents defaults to a HuggingFace inference endpoint
```

The plain-dict conversion path (`smolagents_tool_to_selectable`,
`smolagents_tools_to_catalog`) and the dict-based step-log ingestion
(`from_smolagents_agent`) work **without** the `[smolagents]` extra —
useful for CI fixtures and unit tests that want to exercise routing
without instantiating the smolagents runtime.

## Architecture

```text
User goal
   │
   ▼
contextweaver Router               ← all smolagents tools registered in Catalog
   │ (top-k shortlist for this step)
   ▼
smolagents MultiStepAgent          ← receives only the shortlist via tools=
   │ (Tool.__call__)
   ▼
contextweaver Firewall             ← intercepts large observations
   │ (summary + artifact handle)
   ▼
contextweaver ContextManager       ← phase-specific prompt for the next step
   │ (budgeted ContextPack)
   ▼
LLM
```

You hook contextweaver in at two points: **before each `agent.run()`**
to narrow the available tools to a shortlist, and **after each tool
invocation** to firewall the raw observation before it joins the next
step's prompt.

## Minimal wiring

```python
from smolagents import CodeAgent, Tool, InferenceClientModel

from contextweaver.adapters.smolagents import load_smolagents_catalog
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder


class WikipediaLookupTool(Tool):
    name = "wikipedia_lookup"
    description = "Fetch the lead paragraph of a Wikipedia article."
    inputs = {"topic": {"type": "string", "description": "Article title."}}
    output_type = "string"

    def forward(self, topic: str) -> str:
        ...


# 1. Build a contextweaver Catalog from the full tool list.
all_tools = [WikipediaLookupTool(), WebSearchTool(), CodeInterpreterTool(), ...]
catalog = load_smolagents_catalog(all_tools)

# 2. Compile the routing graph once.
graph = TreeBuilder(max_children=8).build(catalog.all())
router = Router(graph, items=catalog.all(), top_k=3)

# 3. At run time, shortlist tools per-query.
result = router.route("summarize the wikipedia article on type theory")
shortlist = {it.id.removeprefix("smolagents:") for it in result.candidate_items}
agent_tools = [t for t in all_tools if t.name in shortlist]

agent = CodeAgent(tools=agent_tools, model=InferenceClientModel())
answer = agent.run("Summarise the Wikipedia page on type theory in two sentences.")
```

## Step-log ingestion

For long agent runs, ingest the recorded step memory through
contextweaver's `ContextManager` and let it produce a budgeted summary
for the next turn or a downstream agent:

```python
from contextweaver.context.manager import ContextManager
from contextweaver.adapters.smolagents_steps import from_smolagents_agent
from contextweaver.types import Phase

ctx_mgr = ContextManager()
agent.run("kick off the multi-step task")
from_smolagents_agent(agent, into=ctx_mgr)

pack = ctx_mgr.build_sync(phase=Phase.answer, query="summarise progress so far")
# pack.text is a budgeted prompt safe to inject into a follow-up agent.
```

Each smolagents step expands to up to three `ContextItem`s:

| smolagents field            | `ContextItem` kind  | id prefix                  |
|-----------------------------|---------------------|----------------------------|
| `task` (`TaskStep`)         | `user_turn`         | `smolagents:task:`         |
| `system_prompt` (`SystemPromptStep`) | `policy`   | `smolagents:system:`       |
| `plan` (`PlanningStep`)     | `plan_state`        | `smolagents:plan:`         |
| `model_output` (`ActionStep`) | `agent_msg`       | `smolagents:thought:`      |
| `tool_calls[i]` (`ActionStep`) | `tool_call`      | `smolagents:tool_call:`    |
| `observations` (`ActionStep`) | `tool_result`     | `smolagents:observation:`  |
| `final_answer` (`FinalAnswerStep`) | `agent_msg`  | `smolagents:final:`        |

Observations are linked back to their originating tool call via
`ContextItem.parent_id`, so the dependency-closure pass keeps the pair
together when the budget tightens.

## Namespace inference

The adapter infers a namespace from the tool name's prefix (separator =
`.`, `/`, or `_`). A tool named `web_search` lands as:

- `id`: `smolagents:web_search`
- `name`: `search` (namespace prefix stripped)
- `namespace`: `web`

Force a uniform namespace with the `namespace=` argument on
`smolagents_tools_to_catalog`:

```python
catalog = load_smolagents_catalog(all_tools, namespace="research")
```

## Inputs → JSON-Schema mapping

smolagents represents arguments as a flat mapping
`{arg_name: {"type": ..., "description": ..., "nullable": ...}}`.
The adapter converts that into the JSON-Schema shape contextweaver's
router consumes:

- Each input becomes a property under `args_schema.properties`.
- Inputs without `"nullable": True` are added to `args_schema.required`.
- Image / audio / any types pass through untouched in `properties`.

## Troubleshooting

**Q: `CatalogError: smolagents tool ... is missing a non-empty 'name'
attribute`** — Your `Tool` subclass declared `name` as a `ClassVar` /
type annotation rather than a class attribute. Set it as a plain class
attribute: `class MyTool(Tool): name = "..."`.

**Q: `from_smolagents_agent` raises "no .memory attribute"** — You
passed something that's neither a list of step dicts nor a live
`MultiStepAgent`. Either pass `agent.memory.steps` directly or wrap
your fixture as `[{...step1...}, {...step2...}]`.

**Q: Routing scores look low for all candidates** — The router uses
TF-IDF by default. smolagents `Tool.description` strings tend to be a
short imperative; for richer scoring, add representative examples to
`SelectableItem.examples` in a post-processing step, or enable the BM25
backend (`Router(... scorer_backend="bm25")`).

## See also

- [`examples/smolagents_adapter_demo.py`](https://github.com/dgenio/contextweaver/blob/main/examples/smolagents_adapter_demo.py) —
  Runnable demo: 4 tools → catalog → routing → step ingestion.
- [How contextweaver Fits](interop.md) — Positioning page covering the
  policy vs. execution boundary for every supported runtime.
- [`docs/cookbook.md`](cookbook.md#3-bring-your-own-tools) — The
  bring-your-own-tools recipe is the canonical fallback if your
  smolagents setup deviates from the standard `Tool` shape.

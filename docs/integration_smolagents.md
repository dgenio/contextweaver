# smolagents Integration

> Pair contextweaver's bounded-choice routing and context firewall with
> Hugging Face's [smolagents](https://github.com/huggingface/smolagents)
> so `CodeAgent` / `ToolCallingAgent` runs see a focused tool shortlist
> instead of every `Tool` in their registry, and large observations never
> blow up the token budget.

## Why

smolagents is built around the same problem space as contextweaver
("execute tools, keep the context small") but at a different layer:
smolagents owns the agent loop and the model call, contextweaver owns
the prompt that loop hands to the model. Three problems contextweaver
addresses for a multi-step smolagents run:

- **Tool overload.** `CodeAgent.tools_to_call_code` puts every tool's
  signature + docstring into the system prompt on every step. A 25-tool
  agent burns 3-4 K tokens before the first observation.
- **Unbounded observations.** A `web_fetch` returning 30 KB of HTML
  ends up verbatim in the next step's `ActionStep.observations`,
  poisoning every subsequent step.
- **No phase awareness.** The same prompt drives reasoning, tool
  selection, and final-answer synthesis — each phase has different
  needs.

contextweaver fixes all three without forking smolagents. The adapter
is a thin stateless converter (`adapters/`); no smolagents internals
are wrapped.

## Prerequisites

```bash
pip install 'contextweaver[smolagents]'
export HF_TOKEN=hf_...   # smolagents typically defaults to a HF Inference model
```

The plain-dict conversion paths
(`smolagents_tool_to_selectable`, `smolagents_tools_to_catalog`,
`from_smolagents_agent`) work **without** the `[smolagents]` extra —
useful for CI fixtures and unit tests that exercise routing without
spinning up a real model.

## Architecture

```text
User goal
   │
   ▼
contextweaver Router               ← all smolagents tools registered as SelectableItems
   │ (top-k shortlist for this step)
   ▼
smolagents Agent                   ← receives only the shortlist as tools
   │ (Tool.forward)
   ▼
contextweaver Firewall             ← intercepts large observations
   │ (summary + artifact handle)
   ▼
contextweaver ContextManager       ← phase-specific prompt for the next step
   │ (budgeted ContextPack)
   ▼
smolagents Model client → LLM
```

You hook contextweaver in at two points:

1. **Before each `agent.run`** — narrow the available tools to a shortlist.
2. **After each step** — ingest `agent.memory.steps` via
   `from_smolagents_agent` so the prior step's tool calls and
   observations flow through contextweaver's budget-aware pipeline
   instead of accumulating in raw form.

## Minimal wiring

```python
from smolagents import CodeAgent, HfApiModel, Tool

from contextweaver.adapters.smolagents import (
    load_smolagents_catalog,
    from_smolagents_agent,
)
from contextweaver.context.manager import ContextManager
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import Phase


class WebSearch(Tool):
    name = "web_search"
    description = "Search the public web for a query and return top results."
    inputs = {"query": {"type": "string", "description": "User search query."}}
    output_type = "string"

    def forward(self, query: str) -> str:
        ...


class ImageGenerator(Tool):
    name = "image_generator"
    description = "Generate an image from a text description."
    inputs = {"prompt": {"type": "string", "description": "Image description."}}
    output_type = "image"

    def forward(self, prompt: str) -> bytes:
        ...


all_tools = [WebSearch(), ImageGenerator(), ...]

# 1. Build a contextweaver Catalog from the full tool set.
catalog = load_smolagents_catalog(all_tools)

# 2. Compile the routing graph once.
graph = TreeBuilder(max_children=8).build(catalog.all())
router = Router(graph, items=catalog.all(), top_k=3)


# 3. Per-task: narrow tools before constructing the agent.
def run_agent(task: str) -> str:
    result = router.route(task)
    short_ids = {it.id.removeprefix("smolagents:") for it in result.candidate_items}
    short_tools = [t for t in all_tools if t.name in short_ids]
    agent = CodeAgent(model=HfApiModel(), tools=short_tools)
    return agent.run(task)
```

## Step-log ingestion

Use `from_smolagents_agent` to pull a finished agent's `memory.steps`
into contextweaver's event log so a follow-up turn (or a different
agent in the same session) sees only the budget-aware projection:

```python
ctx_mgr = ContextManager()
agent = CodeAgent(model=..., tools=short_tools)
answer = agent.run(task)

from_smolagents_agent(agent, into=ctx_mgr)
pack = ctx_mgr.build_sync(phase=Phase.answer, query="summarise the last run")
```

The mapping is:

| smolagents step                | contextweaver `ItemKind` |
|---|---|
| `TaskStep`                     | `user_turn` |
| `ActionStep.model_output`      | `agent_msg` (free-text reasoning) |
| `ActionStep.tool_calls[*]`     | `tool_call` (one per call) |
| `ActionStep.observations`      | `tool_result` (linked via `parent_id`) |
| `PlanningStep`                 | `plan_state` |
| `FinalAnswerStep`              | `agent_msg` (with `metadata["final_answer"]=True`) |

**Note on `CodeAgent` code blocks.** `CodeAgent` runs emit Python code
that the runtime executes locally. The adapter translates only the
*executed* tool calls into `ContextItem`s — the raw code blocks are
intentionally not ingested. Per #274's acceptance criterion, agents
should reason about the same artefacts the LLM acted on, not the
generated code surface.

## Firewalling observations

Wrap each tool's `forward` so its return value flows through
`ContextManager.ingest_tool_result` before being saved as an
observation:

```python
from contextweaver.context.manager import ContextManager

ctx_mgr = ContextManager()


def firewalled(tool: Tool) -> Tool:
    original = tool.forward

    def _forward(*args, **kwargs):
        raw = original(*args, **kwargs)
        item, _envelope = ctx_mgr.ingest_tool_result(
            tool_call_id=f"{tool.name}:{id(raw)}",
            raw_output=str(raw),
            tool_name=tool.name,
        )
        return item.text  # compact summary; raw addressable in artifact_store

    tool.forward = _forward  # type: ignore[method-assign]
    return tool


short_tools = [firewalled(t) for t in short_tools]
```

## Namespace inference

The adapter infers a namespace from the tool name's prefix (separator =
`.`, `/`, or `_`). A tool named `web_search` lands as:

- `id`: `smolagents:web_search`
- `name`: `search` (namespace prefix stripped)
- `namespace`: `web`

Force a uniform namespace with the `namespace=` argument on
`smolagents_tools_to_catalog`:

```python
catalog = load_smolagents_catalog(all_tools, namespace="hf")
```

## Inputs → JSON Schema

smolagents' `Tool.inputs` is a mapping
`{arg: {"type": "string", "description": "...", "nullable": ?}}`. The
adapter coerces this to a JSON-Schema `properties` + `required` block
on `SelectableItem.args_schema`:

- `nullable=True` → the arg is **not** added to `required`.
- `nullable=False` (or unset) → the arg is added to `required` (sorted alphabetically).
- The `output_type` string is preserved both on `metadata["output_type"]`
  and as a custom `x-smolagents-output-type` field on the schema for
  downstream consumers that care.

## Troubleshooting

**Q: My `CodeAgent` reasoning is missing from the ContextManager** —
By design. The adapter ingests `ActionStep.model_output` (free-text
reasoning) but not the generated code blocks. If you want the code
inline, register a custom `_decode_action_step` shim or copy the body
of `from_smolagents_agent` into your own ingestion routine.

**Q: `from_smolagents_agent` raised `'could not locate ... steps'`** —
You passed an object that exposes neither `memory.steps` nor a top-level
`steps` attribute. Pass either a real `MultiStepAgent`, or a plain list
of step dicts.

**Q: Routing returns the wrong tool for a code-flavoured query** —
smolagents agents often pick tools by function-name lexical match. If
your tool name and docstring don't share keywords with the query, score
will be near zero. Enrich the `description` with synonyms or set
`SelectableItem.examples` after conversion.

## See also

- [`examples/smolagents_adapter_demo.py`](https://github.com/dgenio/contextweaver/blob/main/examples/smolagents_adapter_demo.py)
  — Runnable demo: 4 tools → catalog → routing + step-log ingestion.
- [How contextweaver Fits](interop.md) — Positioning page.
- [smolagents docs](https://huggingface.co/docs/smolagents)
- [smolagents on GitHub](https://github.com/huggingface/smolagents)
- [`docs/cookbook.md`](cookbook.md#3-bring-your-own-tools) — The
  bring-your-own-tools recipe is the canonical fallback if your
  `CodeAgent` setup deviates from the standard `Tool` shape.

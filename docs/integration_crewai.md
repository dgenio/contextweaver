# CrewAI Integration

> Pair contextweaver's bounded-choice routing and context firewall with
> [CrewAI](https://docs.crewai.com/) so role-based agent crews see a
> focused shortlist of tools instead of every `BaseTool` in their
> registry, and large tool results never blow up the prompt budget.

## Why

A CrewAI `Crew` running multi-step tasks across a shared tool registry
hits three problems contextweaver was built for:

- **Tool overload.** Every `BaseTool` description (plus its JSON schema
  preamble — CrewAI ships those to the LLM) is in the system prompt on
  every step. A 30-tool crew burns 4-6 K tokens before any reasoning.
- **Unbounded shared state.** Crews pass tool outputs between agents;
  one multi-KB scrape or transcript poisons every subsequent agent's
  prompt.
- **No phase awareness.** The same instructions drive "pick the next
  tool", "fill in its arguments", and "produce the final answer" —
  they all need different surfaces.

contextweaver fixes all three without forking CrewAI. The adapter is a
thin stateless converter (`adapters/`); no CrewAI internals are wrapped.

## Prerequisites

```bash
pip install 'contextweaver[crewai]'
export OPENAI_API_KEY=sk-...  # CrewAI defaults to OpenAI; bring your own LLM client
```

The plain-dict conversion path (`crewai_tool_to_selectable`,
`crewai_tools_to_catalog`) works **without** the `[crewai]` extra —
useful for CI fixtures and unit tests that want to exercise routing
without instantiating the CrewAI runtime.

## Architecture

```text
User goal
   │
   ▼
contextweaver Router               ← all crew tools registered in Catalog
   │ (top-k shortlist for this step)
   ▼
CrewAI Agent                       ← receives only the shortlist as tools
   │ (BaseTool.run)
   ▼
contextweaver Firewall             ← intercepts large results
   │ (summary + artifact handle)
   ▼
contextweaver ContextManager       ← phase-specific prompt for the next step
   │ (budgeted ContextPack)
   ▼
LLM
```

You hook contextweaver in at two points: **before each agent's `kickoff`**
to narrow the available tools to a shortlist, and **after each tool
invocation** to firewall the raw result before it joins the shared crew
context.

## Minimal wiring

```python
from crewai import Agent, Crew, Task
from crewai.tools import BaseTool

from contextweaver.adapters.crewai import load_crewai_catalog
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder


class SearchTool(BaseTool):
    name: str = "github_search_repos"
    description: str = "Search GitHub repositories by keyword."

    def _run(self, query: str) -> str:
        ...  # your implementation


class CreateIssueTool(BaseTool):
    name: str = "github_create_issue"
    description: str = "Open a new issue on a GitHub repository."

    def _run(self, repo: str, title: str, body: str = "") -> str:
        ...


# 1. Build a contextweaver Catalog from the full set of crew tools.
all_tools = [SearchTool(), CreateIssueTool(), ...]
catalog = load_crewai_catalog(all_tools)

# 2. Compile the routing graph once.
graph = TreeBuilder(max_children=8).build(catalog.all())
router = Router(graph, items=catalog.all(), top_k=3)

# 3. At task assembly time, shortlist tools per-task instead of dumping
#    the full registry into every agent.
result = router.route("find recent typescript repos and open a tracking issue")
shortlist = {it.id.removeprefix("crewai:") for it in result.candidate_items}
agent_tools = [t for t in all_tools if t.name in shortlist]

researcher = Agent(
    role="repo researcher",
    goal="find candidate repositories matching the user request",
    backstory="An information-retrieval specialist.",
    tools=agent_tools,
)
task = Task(
    description="Find five candidate typescript repos and open a tracking issue.",
    expected_output="A list of repo URLs plus the URL of the opened issue.",
    agent=researcher,
)
crew = Crew(agents=[researcher], tasks=[task])
crew.kickoff()
```

## Firewalling tool results

For long-running crews, wrap each tool's `_run` so its return value
flows through `ContextManager.ingest_tool_result` before the next
agent sees it. The simplest pattern is a per-tool decorator:

```python
from contextweaver.context.manager import ContextManager
from contextweaver.types import ContextItem, ItemKind

ctx_mgr = ContextManager()

def firewalled(tool: BaseTool) -> BaseTool:
    """Wrap *tool* so every return value is firewalled before propagation."""
    original_run = tool._run

    def _run(*args: object, **kwargs: object) -> object:
        raw = original_run(*args, **kwargs)
        item, _envelope = ctx_mgr.ingest_tool_result(
            tool_call_id=f"{tool.name}:{id(raw)}",
            raw_output=str(raw),
            tool_name=tool.name,
        )
        # Return the firewalled summary; the raw bytes stay addressable
        # in ctx_mgr.artifact_store and are accessible via drilldown.
        return item.text

    tool._run = _run  # type: ignore[method-assign]
    return tool
```

The same `ContextManager` instance can then build per-phase prompts
(`Phase.call` for argument selection, `Phase.answer` for the crew's
final synthesis) via `ctx_mgr.build_sync(phase=...)`.

## Namespace inference

The adapter infers a namespace from the tool name's prefix (separator =
`.`, `/`, or `_`). A tool named `github_search_repos` lands as:

- `id`: `crewai:github_search_repos`
- `name`: `search_repos` (namespace prefix stripped)
- `namespace`: `github`

Force a uniform namespace with the `namespace=` argument on
`crewai_tools_to_catalog`:

```python
catalog = load_crewai_catalog(all_tools, namespace="my_crew")
```

## CrewAI description-preamble note

CrewAI's `BaseTool.model_dump()` returns a `description` field with the
tool name and a JSON-schema preamble prepended (e.g.
`Tool Name: search\nTool Arguments: {...}\nTool Description: ...`).
contextweaver is intentionally faithful to that enriched form so the
router scores against the same text the LLM eventually sees from CrewAI.
If you need the original description without the preamble, access
`item.metadata["original_description"]` (populated automatically when
the preamble is detected) or convert from a plain dict via
`crewai_tool_to_selectable({...})`.

## Troubleshooting

**Q: `CatalogError: CrewAI tool definition is missing a non-empty 'name'
field`** — Your tool exposes `name` as a class-level Pydantic field but
the model isn't instantiated. Pass `MyTool()` (a live instance), not
`MyTool` (the class), to `load_crewai_catalog`.

**Q: `infer_crewai_namespace` returned `crewai` for my tool** — Your tool
name has no detectable separator (`_` / `.` / `/`). Pass an explicit
`namespace=` to `crewai_tool_to_selectable` if you want a different
default.

**Q: Routing scores look low for all candidates** — The router uses TF-IDF
by default. CrewAI tool descriptions tend to be short imperative
sentences ("Search the corpus."); for richer scoring, add representative
examples to `SelectableItem.examples` in a post-processing step, or
enable the BM25 backend (`Router(... scorer_backend="bm25")`).

## See also

- [`examples/crewai_adapter_demo.py`](https://github.com/dgenio/contextweaver/blob/main/examples/crewai_adapter_demo.py) —
  Runnable demo: 4 tools → catalog → routing.
- [How contextweaver Fits](interop.md) — Positioning page covering the
  policy vs. execution boundary for every supported runtime.
- [`docs/cookbook.md`](cookbook.md#3-bring-your-own-tools) — The
  bring-your-own-tools recipe is the canonical fallback if your CrewAI
  setup deviates from the standard `BaseTool` shape.

# Pydantic AI Integration

> Pair contextweaver's bounded-choice routing and context firewall with
> [Pydantic AI](https://ai.pydantic.dev/) so type-safe agents see a
> focused shortlist of tools instead of every typed `Tool` definition,
> and large tool results never blow up the prompt budget.

## Why

A Pydantic AI `Agent` running with a large `FunctionToolset` (or
multiple toolsets composed via `Agent(toolsets=[...])`) hits two
problems contextweaver was built for:

- **Tool overload.** Every `Tool`'s JSON-Schema-typed parameter
  definition (plus its description) is in the system prompt on every
  run. A 30-tool agent burns 4-6 K tokens before any reasoning.
- **Unbounded message history.** Pydantic AI's `ModelMessage` history
  grows monotonically across multi-turn runs; one large `tool-return`
  part poisons every subsequent turn's prompt.

contextweaver fixes both without forking Pydantic AI. The adapter is a
thin stateless converter (`adapters/`); no Pydantic AI internals are
wrapped.

## Prerequisites

```bash
pip install 'contextweaver[pydantic-ai]'
export OPENAI_API_KEY=sk-...  # Pydantic AI defaults to OpenAI; bring your own model client
```

The plain-dict conversion path (`pydantic_ai_tool_to_selectable`,
`pydantic_ai_tools_to_catalog`) and the dict-based message round-trip
(`from_pydantic_ai_messages` / `to_pydantic_ai_messages`) work
**without** the `[pydantic-ai]` extra — useful for CI fixtures and
unit tests that want to exercise routing without instantiating the
Pydantic AI runtime.

## Architecture

```text
User goal
   │
   ▼
contextweaver Router               ← all Pydantic AI tools registered in Catalog
   │ (top-k shortlist for this turn)
   ▼
Pydantic AI Agent                  ← receives only the shortlist via toolsets=
   │ (Tool.run)
   ▼
contextweaver Firewall             ← intercepts large tool-return parts
   │ (summary + artifact handle)
   ▼
contextweaver ContextManager       ← phase-specific prompt for the next turn
   │ (budgeted ContextPack)
   ▼
LLM
```

You hook contextweaver in at two points: **before each `agent.run()`**
to narrow the available tools to a shortlist, and **after each tool
invocation** to firewall the raw result before it joins the next turn's
message history.

## Minimal wiring

```python
from pydantic_ai import Agent
from pydantic_ai.tools import Tool

from contextweaver.adapters.pydantic_ai import load_pydantic_ai_catalog
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder


async def search_repos(query: str, limit: int = 5) -> list[str]:
    """Search GitHub repositories by keyword."""
    ...


async def create_issue(repo: str, title: str, body: str = "") -> str:
    """Open a new issue on a GitHub repository."""
    ...


# 1. Build a contextweaver Catalog from the full tool set.
all_tools = [Tool(search_repos), Tool(create_issue), ...]
catalog = load_pydantic_ai_catalog(all_tools)

# 2. Compile the routing graph once.
graph = TreeBuilder(max_children=8).build(catalog.all())
router = Router(graph, items=catalog.all(), top_k=3)

# 3. At run time, shortlist tools per-query instead of registering every
#    tool on the Agent.
result = router.route("find recent typescript repos and open a tracking issue")
shortlist = {it.id.removeprefix("pydantic_ai:") for it in result.candidate_items}
agent_tools = [t for t in all_tools if t.name in shortlist]

agent = Agent("openai:gpt-4o-mini", tools=agent_tools)
out = await agent.run("Find five candidate typescript repos and open a tracking issue.")
```

## Message-history round-trip

For multi-turn runs, ingest the prior `ModelMessage` history through
contextweaver's `ContextManager` and let it produce a phase-aware
prompt. The decoder works against the `.model_dump()` shape (or directly
against live `ModelMessage` instances):

```python
from contextweaver.context.manager import ContextManager
from contextweaver.adapters.pydantic_ai_messages import from_pydantic_ai_messages
from contextweaver.types import Phase

ctx_mgr = ContextManager()
prior_run = await agent.run("...")
from_pydantic_ai_messages(prior_run.all_messages(), into=ctx_mgr)

pack = ctx_mgr.build_sync(phase=Phase.answer, query="follow-up question")
# pack.text is the budgeted prompt; feed it into the next agent.run call.
```

The inverse — `to_pydantic_ai_messages` — re-emits a structurally
equivalent `list[ModelMessage]` dict shape for round-tripping back into
Pydantic AI's `message_history=` argument:

```python
from contextweaver.adapters.pydantic_ai_messages import to_pydantic_ai_messages
from pydantic_ai.messages import ModelMessagesTypeAdapter

dumped = to_pydantic_ai_messages(ctx_mgr.event_log.all())
messages = ModelMessagesTypeAdapter.validate_python(dumped)
out = await agent.run("next question", message_history=messages)
```

## Namespace inference

The adapter infers a namespace from the tool name's prefix (separator =
`.`, `/`, or `_`). A tool named `github_search_repos` lands as:

- `id`: `pydantic_ai:github_search_repos`
- `name`: `search_repos` (namespace prefix stripped)
- `namespace`: `github`

Force a uniform namespace with the `namespace=` argument on
`pydantic_ai_tools_to_catalog`:

```python
catalog = load_pydantic_ai_catalog(all_tools, namespace="my_agent")
```

## Troubleshooting

**Q: `CatalogError: Pydantic AI tool ... is missing a non-empty 'name'
attribute`** — Your tool object is the underlying callable rather than a
wrapped `Tool` instance. Wrap it with `Tool(fn)` before passing to
`load_pydantic_ai_catalog`.

**Q: `to_pydantic_ai_messages` raises "missing 'msg_index' metadata"** —
The items being re-encoded didn't come from `from_pydantic_ai_messages`.
Only items produced by the decoder carry the metadata required for the
inverse mapping; mixing in unrelated `ContextItem`s isn't supported.

**Q: Routing scores look low for all candidates** — The router uses
TF-IDF by default. Pydantic AI tool descriptions are typically a single
sentence; for richer scoring, add representative examples to
`SelectableItem.examples` in a post-processing step, or enable the BM25
backend (`Router(... scorer_backend="bm25")`).

## See also

- [`examples/pydantic_ai_adapter_demo.py`](https://github.com/dgenio/contextweaver/blob/main/examples/pydantic_ai_adapter_demo.py) —
  Runnable demo: 4 tools → catalog → routing → message round-trip.
- [How contextweaver Fits](interop.md) — Positioning page covering the
  policy vs. execution boundary for every supported runtime.
- [`docs/cookbook.md`](cookbook.md#3-bring-your-own-tools) — The
  bring-your-own-tools recipe is the canonical fallback if your Pydantic
  AI setup deviates from the standard `Tool` / `FunctionToolset` shape.

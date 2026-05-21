# Agno Integration

> Pair contextweaver's bounded-choice routing and context firewall with
> [Agno](https://github.com/agno-agi/agno) (formerly Phidata) so an
> `Agent` running with multiple `Toolkit`s sees a focused shortlist of
> tools instead of every `Function` entry, and large tool results never
> blow up the prompt budget.

## Why

An Agno `Agent` running multi-step tasks with several `Toolkit`s
attached (`DuckDuckGoTools`, `WikipediaTools`, `YFinanceTools`, …) hits
three problems contextweaver was built for:

- **Tool overload.** Every `Function` from every attached `Toolkit` is
  rendered into the system prompt on every step. A 30-tool agent burns
  4-6 K tokens before any reasoning.
- **Unbounded session memory.** Agno persists `Agent.memory.messages`
  across runs; one large tool response poisons every subsequent run's
  prompt.
- **No phase awareness.** The same instructions drive "pick the next
  tool", "fill in its arguments", and "produce the final answer" —
  they all need different surfaces.

contextweaver fixes all three without forking Agno. The adapter is a
thin stateless converter (`adapters/`); no Agno internals are wrapped.
Agno's own memory / knowledge layer is **complementary**: contextweaver
replaces the *prompt-assembly* step, not Agno's persistence.

## Prerequisites

```bash
pip install 'contextweaver[agno]'
export OPENAI_API_KEY=sk-...  # Agno's OpenAIChat model is the default; bring your own model client
```

The plain-dict conversion path (`agno_tool_to_selectable`,
`agno_tools_to_catalog`) and the dict-based message ingestion
(`from_agno_agent`) work **without** the `[agno]` extra — useful for CI
fixtures and unit tests that want to exercise routing without
instantiating the Agno runtime.

## Architecture

```text
User goal
   │
   ▼
contextweaver Router               ← all Agno tools registered in Catalog
   │ (top-k shortlist for this turn)
   ▼
Agno Agent                         ← receives only the shortlist via tools=
   │ (Function.execute)
   ▼
contextweaver Firewall             ← intercepts large tool responses
   │ (summary + artifact handle)
   ▼
contextweaver ContextManager       ← phase-specific prompt for the next turn
   │ (budgeted ContextPack)
   ▼
LLM
```

You hook contextweaver in at two points: **before each `agent.run()`**
to narrow the available tools to a shortlist, and **after each tool
invocation** to firewall the raw output before it joins the session
memory.

## Minimal wiring

```python
from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.tools.duckduckgo import DuckDuckGoTools
from agno.tools.wikipedia import WikipediaTools
from agno.tools.yfinance import YFinanceTools

from contextweaver.adapters.agno import load_agno_catalog
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder


# 1. Build a contextweaver Catalog from the full toolkit set.
toolkits = [DuckDuckGoTools(), WikipediaTools(), YFinanceTools()]
catalog = load_agno_catalog(toolkits)

# 2. Compile the routing graph once.
graph = TreeBuilder(max_children=8).build(catalog.all())
router = Router(graph, items=catalog.all(), top_k=3)

# 3. At run time, shortlist toolkits per-query.
result = router.route("look up NVIDIA's company info")
shortlist_names = {it.metadata.get("toolkit") for it in result.candidate_items}
agent_toolkits = [tk for tk in toolkits if tk.name in shortlist_names]

agent = Agent(model=OpenAIChat(id="gpt-4o-mini"), tools=agent_toolkits)
agent.run("Fetch NVIDIA's latest company info and a one-line summary.")
```

## Message-history ingestion

For multi-turn sessions, ingest Agno's recorded message history through
contextweaver's `ContextManager` so the next turn's prompt is budgeted
and firewalled:

```python
from contextweaver.context.manager import ContextManager
from contextweaver.adapters.agno_messages import from_agno_agent
from contextweaver.types import Phase

ctx_mgr = ContextManager()
agent.run("first question")
from_agno_agent(agent, into=ctx_mgr)

pack = ctx_mgr.build_sync(phase=Phase.answer, query="follow-up question")
# pack.text is a budgeted prompt; feed it into the next agent.run call as
# additional context, or inject via Agno's `additional_messages=` argument.
```

The decoder prefers `Agent.run_response.messages` (the richer per-run
history with `reasoning_content`) and falls back to `Agent.memory.messages`
or `Agent.memory.get_messages()` for session-level history.

`reasoning_content` from Agno's reasoning models is concatenated with
the assistant content so the firewall pipeline can score the full
deliberation rather than only the user-visible response.

## Namespace inference

The adapter infers a namespace from the tool name's prefix (separator =
`.`, `/`, or `_`). A tool named `duckduckgo_search` lands as:

- `id`: `agno:duckduckgo_search`
- `name`: `search` (namespace prefix stripped)
- `namespace`: `duckduckgo`

When loading a `Toolkit`, the toolkit name is also stamped on each
function's `metadata["toolkit"]` so you can route by toolkit (filter
the shortlist down to a known `Toolkit` instance) rather than by tool
name.

Force a uniform namespace with the `namespace=` argument on
`agno_tools_to_catalog`:

```python
catalog = load_agno_catalog(toolkits, namespace="research")
```

## Composition with Agno's memory / knowledge layer

Agno already ships its own `Memory` (chat history persistence) and
`Knowledge` (RAG over a `VectorDb`) layers. contextweaver does **not**
replace either:

- **Agno memory** persists turns across sessions. contextweaver decides
  *which* of those turns enter the next prompt under the budget.
- **Agno knowledge** retrieves documents from a vector DB.
  contextweaver decides *which* retrieved chunks enter the prompt and
  firewalls large ones.

A typical wiring registers contextweaver as the prompt-compilation
layer between Agno's `Agent.run` and the LLM call, and leaves Agno's
storage layers untouched.

## Troubleshooting

**Q: `CatalogError: Agno tool ... is missing a non-empty 'name'
attribute`** — You passed an unwrapped callable. Wrap it via the
`@tool` decorator or expose it as a `Function` field on a `Toolkit`
subclass before calling `load_agno_catalog`.

**Q: `from_agno_agent` raises "neither .run_response.messages nor
.memory.messages"`** — You passed something that's neither a live
`Agent` nor a `list[dict]`. Either call `agent.run(...)` first (so
`run_response` is populated) or pass `agent.memory.messages` directly.

**Q: Routing scores look low for all candidates** — The router uses
TF-IDF by default. Agno `Function.description` strings tend to be a
single sentence; for richer scoring, add representative examples to
`SelectableItem.examples` in a post-processing step, or enable the BM25
backend (`Router(... scorer_backend="bm25")`).

## See also

- [`examples/agno_adapter_demo.py`](https://github.com/dgenio/contextweaver/blob/main/examples/agno_adapter_demo.py) —
  Runnable demo: 4 tools → catalog → routing → message ingestion.
- [How contextweaver Fits](interop.md) — Positioning page covering the
  policy vs. execution boundary for every supported runtime.
- [`docs/cookbook.md`](cookbook.md#3-bring-your-own-tools) — The
  bring-your-own-tools recipe is the canonical fallback if your Agno
  setup deviates from the standard `Toolkit` / `Function` shape.

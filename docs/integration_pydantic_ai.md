# Pydantic AI Integration

> Pair contextweaver's bounded-choice routing, context firewall, and
> phase-budgeted prompt assembly with [Pydantic AI](https://ai.pydantic.dev/)
> so type-safe agents see a focused shortlist of tools instead of every
> `Tool` in their registry, and large tool returns never blow up the
> token budget.

## Why

A Pydantic AI `Agent` running multi-turn tasks hits three problems
contextweaver was built for:

- **Tool overload.** Every `Tool`'s description and JSON Schema is in the
  system prompt on every step. A 30-tool agent burns 3-5 K tokens before
  any reasoning happens.
- **Unbounded tool returns.** A `ToolReturnPart.content` (e.g. a 20 KB
  search payload) ends up in the next request's `parts` list, poisoning
  every subsequent turn.
- **No phase awareness.** The same prompt drives "pick a tool", "fill
  in its arguments", and "produce the final answer" — they all need
  different surfaces.

contextweaver fixes all three without forking Pydantic AI. The adapter
is a thin stateless converter (`adapters/`); no Pydantic AI internals
are wrapped.

## Prerequisites

```bash
pip install 'contextweaver[pydantic-ai]'
export OPENAI_API_KEY=sk-...   # or any provider Pydantic AI supports
```

The plain-dict conversion path (`pydantic_ai_tool_to_selectable`,
`pydantic_ai_tools_to_catalog`, `from_pydantic_ai_messages`) works
**without** the `[pydantic-ai]` extra — useful for CI fixtures and
unit tests that exercise routing without instantiating the runtime.

## Architecture

```text
User goal
   │
   ▼
contextweaver Router               ← all Pydantic AI tools registered as SelectableItems
   │ (top-k shortlist for this step)
   ▼
Pydantic AI Agent                  ← receives only the shortlist as tools
   │ (Tool.run)
   ▼
contextweaver Firewall             ← intercepts large ToolReturnPart contents
   │ (summary + artifact handle)
   ▼
contextweaver ContextManager       ← phase-specific prompt for the next step
   │ (budgeted ContextPack)
   ▼
Pydantic AI Model client → LLM
```

You hook contextweaver in at three points:

1. **Before each `Agent.run`** — narrow the available tools to a shortlist.
2. **After each tool invocation** — firewall the raw result before it
   joins the message history.
3. **Between turns** — ingest the prior turn's `ModelMessage`s and rebuild
   a phase-specific prompt for the next call.

## Minimal wiring

```python
from pydantic_ai import Agent, Tool

from contextweaver.adapters.pydantic_ai import (
    load_pydantic_ai_catalog,
    from_pydantic_ai_messages,
)
from contextweaver.context.manager import ContextManager
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import Phase


def search_repos(query: str, limit: int = 10) -> list[str]:
    """Search GitHub repositories by keyword."""
    ...


def open_issue(repo: str, title: str, body: str = "") -> str:
    """Open a new issue on a GitHub repository."""
    ...


all_tools = [Tool(search_repos), Tool(open_issue), ...]

# 1. Build a contextweaver Catalog from the full set of agent tools.
catalog = load_pydantic_ai_catalog(all_tools)

# 2. Compile the routing graph once.
graph = TreeBuilder(max_children=8).build(catalog.all())
router = Router(graph, items=catalog.all(), top_k=3)

# 3. Per-turn: shortlist tools, then build a Pydantic AI Agent with only those.
def build_agent_for_turn(query: str) -> Agent:
    result = router.route(query)
    short_ids = {it.id.removeprefix("pydantic_ai:") for it in result.candidate_items}
    short_tools = [t for t in all_tools if t.function.__name__ in short_ids]
    return Agent("openai:gpt-4o-mini", tools=short_tools)


# 4. Maintain a single ContextManager across turns; ingest prior messages
#    so phase-budgeted prompts include the relevant history.
ctx_mgr = ContextManager()
agent = build_agent_for_turn(query="open a tracking issue for the typescript work")
result = agent.run_sync("open a tracking issue for the typescript work")
from_pydantic_ai_messages(
    [m.model_dump() for m in result.all_messages()],
    into=ctx_mgr,
)
pack = ctx_mgr.build_sync(phase=Phase.answer, query="summarise the tracking work")
```

## Message round-trip

`from_pydantic_ai_messages` / `to_pydantic_ai_messages` are a lossless
pair for any well-formed transcript. The mapping is:

| Pydantic AI part            | contextweaver `ItemKind` |
|---|---|
| `system-prompt`             | `policy` |
| `user-prompt`               | `user_turn` |
| `retry-prompt`              | `user_turn` (with `metadata["retry"]=True`) |
| `tool-call`                 | `tool_call` |
| `tool-return`               | `tool_result` (with `parent_id` linking the call) |
| `text` (response)           | `agent_msg` |

`tool_call_id` round-trips through `ContextItem.id` so the encoder
reconstructs the original `ModelMessage` sequence byte-for-byte.

Use the round-trip when you want contextweaver to compute a budget-aware
prompt and then hand the result back to Pydantic AI as its
`message_history` argument:

```python
items = from_pydantic_ai_messages([m.model_dump() for m in result.all_messages()])
# … contextweaver-side processing (firewall, scoring, dedup) …
trimmed = to_pydantic_ai_messages(filtered_items)
next_result = agent.run_sync("follow up question", message_history=trimmed)
```

## Firewalling tool results

Wrap each tool's body so its return value flows through
`ContextManager.ingest_tool_result` before the next turn sees it. The
simplest pattern is a decorator:

```python
from contextweaver.context.manager import ContextManager

ctx_mgr = ContextManager()


def firewalled(fn):
    """Wrap *fn* so every return value is firewalled before propagation."""
    def wrapper(*args, **kwargs):
        raw = fn(*args, **kwargs)
        item, _envelope = ctx_mgr.ingest_tool_result(
            tool_call_id=f"{fn.__name__}:{id(raw)}",
            raw_output=str(raw),
            tool_name=fn.__name__,
        )
        return item.text  # compact summary; raw bytes addressable in artifact_store

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


@firewalled
def search_repos(query: str, limit: int = 10) -> list[str]:
    """Search GitHub repositories."""
    ...
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

**Q: `CatalogError: Pydantic AI tool definition is missing a non-empty
'name' field`** — Your tool object exposes `name` only at class level
without a working `model_dump()`. Either pass a live `Tool(...)` instance
(which has a populated `__init__`) or convert from a dict via
`pydantic_ai_tool_to_selectable({...})`.

**Q: `to_pydantic_ai_messages` returned an empty list** — The encoder
filters out items without `metadata["provider"] == "pydantic_ai"`. If
you mixed items from other adapters into the list, they get skipped.
Decode and re-encode in the same provider scope.

**Q: Routing scores look low for all candidates** — The router uses
TF-IDF by default. Pydantic AI tool descriptions are typically the
Google-style docstring's first sentence. For richer scoring, set richer
descriptions on the `Tool(...)` constructor or enable the BM25 backend
(`Router(... scorer_backend="bm25")`).

## See also

- [`examples/pydantic_ai_adapter_demo.py`](https://github.com/dgenio/contextweaver/blob/main/examples/pydantic_ai_adapter_demo.py)
  — Runnable demo: 4 tools → catalog → routing + message round-trip.
- [How contextweaver Fits](interop.md) — Positioning page covering the
  policy vs. execution boundary for every supported runtime.
- [Pydantic AI docs](https://ai.pydantic.dev/)
- [Pydantic AI message types](https://ai.pydantic.dev/api/messages/)
- [`docs/cookbook.md`](cookbook.md#3-bring-your-own-tools) — The
  bring-your-own-tools recipe is the canonical fallback if your tool
  setup deviates from the standard `Tool` shape.

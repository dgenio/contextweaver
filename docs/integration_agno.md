# Agno Integration

> Pair contextweaver's bounded-choice routing, context firewall, and
> phase-budgeted prompt assembly with [Agno](https://github.com/agno-agi/agno)
> (formerly Phidata) so agents see a focused shortlist of tools instead
> of every `Function` in their `Toolkit`s, and large tool outputs never
> blow up the token budget.

## Agno's Memory ≠ contextweaver

> **Read this section first if you skipped it before.**

Agno ships its own `Memory` / `Storage` / `Knowledge` layer. contextweaver
**does not replace those.** The two layers solve different problems:

| Concern                                       | Owned by               |
|---|---|
| Cross-session persistence (PostgreSQL, SQLite, Mongo) | **Agno** `Storage`        |
| Vector recall over prior conversations         | **Agno** `Memory`         |
| Knowledge-base / RAG over docs                 | **Agno** `Knowledge`      |
| Per-turn prompt assembly under a token budget  | **contextweaver**         |
| Tool catalog routing (`ChoiceCards`)           | **contextweaver**         |
| Context firewall on raw tool outputs           | **contextweaver**         |

Agno keeps the durable state; contextweaver decides which fraction of
that state lands in the next prompt. When the two cooperate, Agno's
`Memory.get_relevant_memories(...)` feeds contextweaver as
`ContextItem(kind=memory_fact, ...)`s, then contextweaver scores them
against the current query under the same budget pressure as everything
else.

This is the same pattern as the Mem0 / Zep / LangMem integration tracked
under [issue #195](https://github.com/dgenio/contextweaver/issues/195)
— the external memory system stays authoritative; contextweaver decides
what makes it into the next prompt.

## Prerequisites

```bash
pip install 'contextweaver[agno]'
export OPENAI_API_KEY=sk-...   # or any provider Agno supports
```

The plain-dict conversion paths (`agno_tool_to_selectable`,
`agno_tools_to_catalog`, `from_agno_session`) work **without** the
`[agno]` extra — useful for CI fixtures and unit tests that exercise
routing without instantiating the Agno runtime.

## Architecture

```text
User goal
   │
   ▼
contextweaver Router               ← all Agno functions registered as SelectableItems
   │ (top-k shortlist for this step)
   ▼
Agno Agent                         ← receives only the shortlist as tools
   │ (Function.entrypoint)
   ▼
contextweaver Firewall             ← intercepts large tool outputs
   │ (summary + artifact handle)
   ▼
contextweaver ContextManager       ← phase-specific prompt for the next step
   │ (budgeted ContextPack)
   ▼
Agno Model client → LLM
```

You hook contextweaver in at two points:

1. **Before `Agent.run`** — narrow available tools.
2. **Between runs** — ingest the prior `AgentSession.runs[*].messages`
   so a follow-up turn sees the budget-aware projection rather than the
   raw transcript.

## Minimal wiring

```python
from agno.agent import Agent
from agno.tools.duckduckgo import DuckDuckGoTools
from agno.tools.yfinance import YFinanceTools

from contextweaver.adapters.agno import (
    load_agno_catalog,
    from_agno_session,
)
from contextweaver.context.manager import ContextManager
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import Phase

# 1. Build a contextweaver Catalog from the full set of Agno toolkits.
toolkits = [DuckDuckGoTools(), YFinanceTools()]
catalog = load_agno_catalog(toolkits)

# 2. Compile the routing graph once.
graph = TreeBuilder(max_children=8).build(catalog.all())
router = Router(graph, items=catalog.all(), top_k=3)


# 3. Per-turn: narrow the toolkits before instantiating the Agent.
def run_turn(query: str) -> str:
    result = router.route(query)
    short_names = {
        it.id.removeprefix("agno:") for it in result.candidate_items
    }
    # Filter each toolkit's functions dict to keep only the routed members.
    filtered: list = []
    for tk in toolkits:
        kept = {
            name: fn for name, fn in tk.functions.items()
            if f"{tk.name}_{name}" in short_names or name in short_names
        }
        if kept:
            tk.functions = kept
            filtered.append(tk)
    agent = Agent(model=..., tools=filtered)
    return agent.run(query)


# 4. Maintain a single ContextManager across turns; ingest the prior session.
ctx_mgr = ContextManager()
session = agent.session  # an AgentSession; carries .runs / .messages
from_agno_session(session, into=ctx_mgr)
pack = ctx_mgr.build_sync(phase=Phase.answer, query="recap the prior turn")
```

## Session-history ingestion

`from_agno_session` accepts an `AgentSession`, an `AgentRun`, or a plain
list of message dicts. Agno follows the OpenAI Chat Completions message
shape closely, so the mapping is identical to the OpenAI adapter:

| Agno message role              | contextweaver `ItemKind` |
|---|---|
| `system`                        | `policy` |
| `user`                          | `user_turn` |
| `assistant` (text content)      | `agent_msg` |
| `assistant.tool_calls[*]`       | `tool_call` |
| `tool` (with `tool_call_id`)    | `tool_result` (linked via `parent_id`) |

`tool_call_id` round-trips through `ContextItem.id` so the linkage from
tool result back to the originating call is preserved on disk and in
serialised event logs.

## Firewalling tool outputs

Wrap each function so its return value flows through
`ContextManager.ingest_tool_result` before being saved as a tool
message:

```python
from contextweaver.context.manager import ContextManager

ctx_mgr = ContextManager()


def firewalled(fn):
    """Wrap an Agno-decorated callable so returns are firewalled."""
    def wrapper(*args, **kwargs):
        raw = fn(*args, **kwargs)
        item, _envelope = ctx_mgr.ingest_tool_result(
            tool_call_id=f"{fn.__name__}:{id(raw)}",
            raw_output=str(raw),
            tool_name=fn.__name__,
        )
        return item.text  # compact summary; raw addressable in artifact_store

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper
```

Apply per function inside a `Toolkit.__init__` for systematic coverage.

## Namespace inference

The adapter uses the `Toolkit.name` (when present) as the namespace,
falling back to underscore-prefix inference. A function named
`yfinance_get_company_info` inside `YFinanceTools(name="yfinance")`
lands as:

- `id`: `agno:yfinance_get_company_info`
- `name`: `get_company_info` (namespace prefix stripped)
- `namespace`: `yfinance`
- `metadata["toolkit_name"]`: `yfinance`

Force a uniform namespace with the `namespace=` argument on
`load_agno_catalog`:

```python
catalog = load_agno_catalog(toolkits, namespace="my_agent")
```

## Troubleshooting

**Q: `CatalogError: Agno tool definition is missing a non-empty 'name'`**
— Your function is a bare callable without `__name__` (e.g. a `lambda`).
Wrap it with `agno.tools.tool(...)` or pass a `Function` instance.

**Q: `from_agno_session` raised `'could not locate ... messages or runs'`**
— You passed an object that exposes neither `messages` nor a `runs`
list. Either pass a real `AgentSession` / `AgentRun`, or a plain list
of message dicts.

**Q: `unknown tool_call_id`** — A `role="tool"` message references a
`tool_call_id` that no prior assistant message announced. This usually
means you sliced a transcript mid-conversation. Either ingest from the
session start, or filter the orphan tool message out before passing it
to the adapter.

**Q: I want to keep Agno's `Memory` and use contextweaver only for
routing** — That works out of the box. Drop the session-ingestion
step; just route tools per turn. Agno's `Memory` continues to feed its
own prompt assembly, and contextweaver focuses on tool selection.

## See also

- [`examples/agno_adapter_demo.py`](https://github.com/dgenio/contextweaver/blob/main/examples/agno_adapter_demo.py)
  — Runnable demo: 4 tools (across 2 toolkits) → catalog → routing +
  session ingestion.
- [How contextweaver Fits](interop.md) — Positioning page.
- [Agno docs](https://docs.agno.com/)
- [Agno on GitHub](https://github.com/agno-agi/agno)
- [`docs/integration_memory.md`](integration_memory.md) — External
  memory adapter patterns (Mem0 today; Zep / LangMem planned). The
  Agno `Memory` layering note in this guide applies equally to those
  systems.
- [`docs/cookbook.md`](cookbook.md#3-bring-your-own-tools) — The
  bring-your-own-tools recipe is the canonical fallback if your Agno
  setup deviates from the standard `Toolkit` / `Function` shape.

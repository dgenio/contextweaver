# LlamaIndex Integration

> Pair contextweaver's bounded-choice routing, phase-specific context, and
> context firewall with [LlamaIndex](https://docs.llamaindex.ai/)'s
> `ReActAgent` so the LLM sees a focused shortlist of tools and a
> budgeted prompt instead of the entire catalogue and conversation
> history.

## Why

A LlamaIndex `ReActAgent` doing function-calling over a large catalogue
runs into three concrete problems:

- **Tool overload.** Every tool's name + description goes into the system
  prompt. With 50+ tools that's thousands of tokens before the user even
  speaks.
- **Unbounded context.** The agent's chat memory grows turn by turn; once
  a tool returns a multi-KB blob, every subsequent turn pays for it.
- **No phase awareness.** The same prompt is used for "which tool?",
  "what arguments?", and "what's the final answer?" — they all need
  different things.

contextweaver fixes all three without forking LlamaIndex.

## Prerequisites

```bash
pip install contextweaver llama-index llama-index-llms-openai
export OPENAI_API_KEY=sk-...
```

## Architecture

```text
User query
   │
   ▼
contextweaver Router            ← all tools registered in Catalog
   │ (top-k shortlist)
   ▼
LlamaIndex ReActAgent           ← receives only the shortlist as tools
   │ (function call)
   ▼
contextweaver Firewall          ← intercepts large results
   │ (summary + artifact handle)
   ▼
contextweaver ContextManager    ← phase-specific prompt compilation
   │ (budgeted ContextPack)
   ▼
LLM
```

You hook contextweaver in at two points: **before tool selection** to
narrow the tool list, and **after each tool call** to firewall the raw
result.

## Minimal wiring

```python
from llama_index.core.agent import ReActAgent
from llama_index.core.llms import ChatMessage, MessageRole
from llama_index.core.tools import FunctionTool
from llama_index.llms.openai import OpenAI

from contextweaver.context.manager import ContextManager
from contextweaver.routing.catalog import Catalog
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import ContextItem, ItemKind, Phase, SelectableItem


def db_query(sql: str) -> str:
    """Execute a read-only SQL query and return JSON rows."""
    return '{"rows": [...]}'   # imagine a 5 KB response


def send_email(to: str, body: str) -> str:
    """Send an email to *to* with *body*."""
    return "ok"


# 1. Register every tool in contextweaver's Catalog as a SelectableItem.
catalog = Catalog()
for fn in (db_query, send_email):
    catalog.register(SelectableItem(
        id=fn.__name__,
        kind="tool",
        name=fn.__name__,
        description=(fn.__doc__ or "").strip().splitlines()[0],
        namespace=fn.__name__.split("_", 1)[0],
    ))
graph = TreeBuilder(max_children=8).build(catalog.all())
router = Router(graph, items=catalog.all(), top_k=3)

# 2. One ContextManager per session.
ctx_mgr = ContextManager()

# 3. Per-turn loop.
def respond(user_query: str, turn: int) -> str:
    # Ingest the user turn.
    ctx_mgr.ingest_sync(ContextItem(
        id=f"u{turn}", kind=ItemKind.user_turn, text=user_query,
    ))

    # Route to top-k tools (LlamaIndex never sees the full catalogue).
    routed = router.route(user_query)
    selected = [
        FunctionTool.from_defaults(fn=locals_lookup[tid])
        for tid in routed.candidate_ids
    ]

    # Hand the shortlist + budgeted prompt to LlamaIndex.  pack.prompt is a
    # plain string; LlamaIndex's chat_history expects a list[ChatMessage],
    # so we wrap it as a single SYSTEM message that primes the agent.
    pack = ctx_mgr.build_sync(phase=Phase.answer, query=user_query)
    agent = ReActAgent.from_tools(selected, llm=OpenAI(model="gpt-4"))
    response = agent.chat(
        user_query,
        chat_history=[ChatMessage(role=MessageRole.SYSTEM, content=pack.prompt)],
    )
    return str(response)
```

`locals_lookup` is whatever map your runtime uses to resolve a tool ID
back to its Python implementation; the routing layer is intentionally
just IDs and scores.

## Firewalling tool results

`ReActAgent` exposes the underlying tool callable; wrap it so the raw
output flows through the context firewall before LlamaIndex sees it:

```python
from llama_index.core.tools import FunctionTool

def _firewalled(fn, tool_call_id: str):
    def wrapped(*args, **kwargs):
        raw = fn(*args, **kwargs)
        item, _envelope = ctx_mgr.ingest_tool_result_sync(
            tool_call_id=tool_call_id,
            raw_output=str(raw),
            tool_name=fn.__name__,
        )
        # item.text is the firewall summary; the raw bytes are in
        # ctx_mgr.artifact_store under item.artifact_ref.handle.
        return item.text
    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    return wrapped

tools = [
    FunctionTool.from_defaults(fn=_firewalled(db_query, "tc-1")),
    FunctionTool.from_defaults(fn=_firewalled(send_email, "tc-2")),
]
```

Inside the agent the LLM sees a compact summary; if it needs more detail
it can ask for a drilldown (see the
[firewall + drilldown cookbook recipe](cookbook.md#4-firewall-drilldown-for-large-tool-outputs)).

## Phase-specific budgets

You usually want different budgets per phase. Pass a `ContextBudget` to
`ContextManager` once and call `build_sync(phase=...)` everywhere:

```python
from contextweaver.config import ContextBudget

ctx_mgr = ContextManager(
    budget=ContextBudget(route=500, call=1200, interpret=1500, answer=3000),
)
```

LlamaIndex's `ReActAgent` doesn't have a built-in concept of phases, so
the typical pattern is:

- `Phase.route` — when selecting which tool to call (via `router.route()`)
- `Phase.call` — when assembling arguments (the agent already does this
  with the schema; use this phase if you build your own ReAct trace)
- `Phase.interpret` — right after the tool returns, when the model
  decides what to do next
- `Phase.answer` — when generating the final user-facing reply

## Advanced patterns

- **Custom phase budgets** for long RAG retrievals — bump `interpret`
  and `answer` budgets so a multi-KB chunk has room to land.
- **Episodic memory across sessions** — store `pack.stats.to_dict()` and
  the final agent response in an `EpisodicStore` for the next session.
- **Fact extraction** — the firewall pulls structured facts out of
  `tool_result` items by default; expose them via
  `ResultEnvelope.facts` to build a per-session knowledge base.
- **Custom retrieval backend** — register a BM25 / fuzzy retriever via
  `engine_registry` (see issue
  [#47](https://github.com/dgenio/contextweaver/issues/47)) when LlamaIndex
  is already producing embeddings and you'd rather route on those.

## Troubleshooting

- **`agent.chat()` answers as if it has no memory.** You probably didn't
  pass `pack.prompt` into the call. LlamaIndex won't pick it up from
  contextweaver implicitly — you compile the context, you inject it (as
  `chat_history=[ChatMessage(role=MessageRole.SYSTEM, content=pack.prompt)]`,
  not as a bare string).
- **The firewall summary is too short.** Override `Summarizer` on
  `ContextManager(summarizer=...)`; the default is a 500-char truncation
  of the first paragraph, deliberately conservative.
- **The router skips a tool you know is relevant.** Check
  `result.scores` — TF-IDF on short descriptions can lose to keyword
  collisions. Add tags or tweak the description, or use
  `context_hints=[...]` (see issue
  [#116](https://github.com/dgenio/contextweaver/issues/116)).
- **Budget overrun.** Inspect `pack.stats` after every build — the
  `dropped_reasons` map tells you exactly which stage rejected what.

## See also

- [How contextweaver Fits](interop.md) — the boundary diagram and what is
  intentionally not contextweaver's job
- [Cookbook](cookbook.md) — copy-paste recipes (FastMCP, A2A, BYOT,
  firewall + drilldown)
- [LlamaIndex docs](https://docs.llamaindex.ai/en/stable/) ·
  [`ReActAgent` reference](https://docs.llamaindex.ai/en/stable/api_reference/agent.html)
- Tracking issue: [#77](https://github.com/dgenio/contextweaver/issues/77)

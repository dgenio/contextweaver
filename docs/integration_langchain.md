# LangChain + LangGraph Integration

> Pair contextweaver with [LangChain](https://python.langchain.com/) and
> [LangGraph](https://langchain-ai.github.io/langgraph/) so chain-based
> and graph-based agents get budget-aware memory, bounded tool routing,
> and a context firewall — without giving up the framework's agent loop.

## Why

LangChain's memory classes (`ConversationBufferMemory`,
`ConversationSummaryMemory`) and LangGraph's stateful checkpoints both
accumulate conversation history with **no token-budget enforcement**.
Long sessions blow through the model's context window; large tool
outputs (multi-KB JSON, RAG retrievals) bloat the prompt; loading every
tool into the system message wastes thousands of tokens.

contextweaver provides three composable replacements:

| Pain | contextweaver answer |
|---|---|
| Unbounded `ConversationBufferMemory` | `ContextManager.build_sync(phase=…)` returns a phase-specific, budgeted prompt |
| Loading every tool into the LLM | `Router.route(query)` returns a bounded shortlist |
| Large tool results bloat the prompt | `ContextManager.ingest_tool_result_sync()` firewalls raw bytes to the artifact store |

The repo already ships a runnable LangChain memory-replacement demo:
[`examples/langchain_memory_demo.py`](https://github.com/dgenio/contextweaver/blob/main/examples/langchain_memory_demo.py)
(installable via `pip install 'contextweaver[langchain]'`). Read that
first — it shows the token-budget delta side by side.

## Prerequisites

```bash
pip install 'contextweaver[langchain]' langchain langchain-openai langgraph
export OPENAI_API_KEY=sk-...
```

## LangChain Integration

### Architecture

```text
User query
   │
   ▼
contextweaver Router         ← all tools (Catalog)
   │ (top-k shortlist)
   ▼
LangChain Agent              ← receives only the shortlist
   │ (tool call)
   ▼
contextweaver Firewall       ← intercepts large results
   │ (summary)
   ▼
contextweaver ContextManager ← phase-specific compilation
   │ (pack.prompt)
   ▼
LLM
```

### Memory replacement

`ConversationBufferMemory` is the canonical "memory" you want to replace:

```python
# Before — unbounded memory
from langchain.memory import ConversationBufferMemory
memory = ConversationBufferMemory()
```

```python
# After — budget-aware memory via contextweaver
from contextweaver.context.manager import ContextManager
from contextweaver.config import ContextBudget
from contextweaver.types import ContextItem, ItemKind, Phase

ctx_mgr = ContextManager(
    budget=ContextBudget(route=500, call=1200, interpret=1500, answer=3000),
)
```

To get the conversation history that a LangChain agent expects, build a
prompt for the relevant phase right before invoking the agent:

```python
def respond(user_query: str, turn: int) -> str:
    # 1. Ingest the user turn into the event log.
    ctx_mgr.ingest_sync(ContextItem(
        id=f"u{turn}", kind=ItemKind.user_turn, text=user_query,
    ))

    # 2. Compile a phase-specific, budgeted prompt.
    pack = ctx_mgr.build_sync(phase=Phase.answer, query=user_query)

    # 3. Invoke the LangChain agent with the compiled prompt as "history".
    result = agent_executor.invoke({"input": user_query, "history": pack.prompt})

    # 4. Ingest the agent's response so the next turn can see it.
    ctx_mgr.ingest_sync(ContextItem(
        id=f"a{turn}", kind=ItemKind.agent_msg, text=result["output"],
    ))
    return result["output"]
```

`pack.stats.included_count` / `dropped_count` tell you exactly what was
kept and what was dropped by the budget — surface these in logs.

### Firewalling tool results via a callback

LangChain emits a `BaseCallbackHandler.on_tool_end()` event after every
tool finishes. That's the natural hook for the context firewall:

```python
from langchain_core.callbacks import BaseCallbackHandler

class ContextWeaverCallback(BaseCallbackHandler):
    """Route LangChain tool results through contextweaver's firewall."""

    def __init__(self, ctx_mgr: ContextManager) -> None:
        self._mgr = ctx_mgr
        self._call_counter = 0

    def on_tool_end(self, output: str, *, name: str, **_: object) -> None:
        self._call_counter += 1
        tool_call_id = f"tc-{self._call_counter}"
        self._mgr.ingest_tool_result_sync(
            tool_call_id=tool_call_id,
            raw_output=str(output),
            tool_name=name,
        )
```

Wire it into your `AgentExecutor`:

```python
from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(model="gpt-4")
agent = create_openai_functions_agent(llm, tools, prompt)
agent_executor = AgentExecutor(
    agent=agent,
    tools=tools,
    callbacks=[ContextWeaverCallback(ctx_mgr)],
)
```

### Routing to a tool shortlist

Use `Router.route()` to narrow the tool list **before** building the
agent:

```python
from contextweaver.routing.catalog import Catalog
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder

# (build Catalog of SelectableItems from your tool definitions)
graph = TreeBuilder().build(catalog.all())
router = Router(graph, items=catalog.all(), top_k=3)

shortlist_ids = router.route(user_query).candidate_ids
selected_tools = [t for t in all_tools if t.name in shortlist_ids]
agent = create_openai_functions_agent(llm, selected_tools, prompt)
```

## LangGraph Integration

LangGraph state is the natural place to park a `ContextManager` because
each node sees the same state object.

### Architecture

```text
User query
   │
   ▼
[LangGraph State]                ← ctx_mgr lives here
   │
   ▼
node: route                       ← ctx_mgr.build_sync(phase=Phase.route)
   │ (Router.route → shortlist)
   ▼
node: call_tool                   ← framework calls the tool
   │ (ctx_mgr.ingest_tool_result_sync — firewall)
   ▼
node: interpret                   ← ctx_mgr.build_sync(phase=Phase.interpret)
   │
   ▼
node: answer                      ← ctx_mgr.build_sync(phase=Phase.answer)
   │
   ▼
LLM
```

### Stateful 4-node graph

```python
from typing import TypedDict

from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

from contextweaver.context.manager import ContextManager
from contextweaver.routing.router import Router
from contextweaver.types import ContextItem, ItemKind, Phase


class AgentState(TypedDict):
    user_query: str
    routed_tools: list[str]
    tool_result: str
    answer: str
    turn: int


ctx_mgr = ContextManager()
# `router` and `llm` from the earlier sections


def route_node(state: AgentState) -> AgentState:
    ctx_mgr.ingest_sync(ContextItem(
        id=f"u{state['turn']}", kind=ItemKind.user_turn, text=state["user_query"],
    ))
    routed = router.route(state["user_query"])
    return {**state, "routed_tools": list(routed.candidate_ids)}


def call_tool_node(state: AgentState) -> AgentState:
    tool_id = state["routed_tools"][0]
    raw = execute_tool(tool_id, state["user_query"])   # your runtime
    ctx_mgr.ingest_sync(ContextItem(
        id=f"tc-{state['turn']}", kind=ItemKind.tool_call,
        text=f"{tool_id}(...)", parent_id=f"u{state['turn']}",
    ))
    item, _ = ctx_mgr.ingest_tool_result_sync(
        tool_call_id=f"tc-{state['turn']}",
        raw_output=str(raw),
        tool_name=tool_id,
    )
    return {**state, "tool_result": item.text}


def answer_node(state: AgentState) -> AgentState:
    pack = ctx_mgr.build_sync(phase=Phase.answer, query=state["user_query"])
    answer = ChatOpenAI(model="gpt-4").invoke(pack.prompt)
    return {**state, "answer": str(answer.content)}


graph = StateGraph(AgentState)
graph.add_node("route",     route_node)
graph.add_node("call_tool", call_tool_node)
graph.add_node("answer",    answer_node)
graph.add_edge("route", "call_tool")
graph.add_edge("call_tool", "answer")
graph.add_edge("answer", END)
graph.set_entry_point("route")
app = graph.compile()

result = app.invoke({"user_query": "...", "routed_tools": [], "tool_result": "",
                     "answer": "", "turn": 1})
```

Putting `ctx_mgr` in the closure (rather than the state itself) keeps the
state pickle-friendly for LangGraph checkpoints.

## Migration guide — LangChain memory → contextweaver

| LangChain pattern | contextweaver equivalent |
|---|---|
| `ConversationBufferMemory()` | `ContextManager(budget=ContextBudget(...))` |
| `ConversationSummaryMemory(llm=...)` | `ContextManager(summarizer=YourSummarizer())` — implement the `Summarizer` protocol |
| `memory.load_memory_variables({})` | `ctx_mgr.build_sync(phase=Phase.answer, query=user_query).prompt` |
| `memory.save_context(in, out)` | `ctx_mgr.ingest_sync(ContextItem(kind=ItemKind.user_turn, ...))` + `ctx_mgr.ingest_sync(ContextItem(kind=ItemKind.agent_msg, ...))` |

See [`examples/langchain_memory_demo.py`](https://github.com/dgenio/contextweaver/blob/main/examples/langchain_memory_demo.py)
for a runnable side-by-side comparison.

## Troubleshooting

- **`AgentExecutor` ignores `pack.prompt`.** Make sure the prompt template
  has a `"{history}"` placeholder (or whichever variable you injected the
  pack into) — LangChain does not auto-discover it.
- **Callbacks not firing.** Confirm you passed `callbacks=[...]` to the
  `AgentExecutor`, not just to the `LLM`. `on_tool_end` is on the executor.
- **LangGraph checkpoints fail to pickle.** Keep `ContextManager` outside
  the `TypedDict` state — it holds an in-memory event log that isn't
  serialised by default. Persist `mgr.event_log.to_dict()` separately
  alongside the checkpoint if you need cross-session continuity.
- **Tool not in the shortlist.** Inspect `result.scores` — the TF-IDF
  retriever may need richer descriptions or tags. You can also use
  `context_hints=[...]` to inject conversation context into scoring.

## See also

- [How contextweaver Fits](interop.md) — boundary, hook points, non-goals
- [Cookbook](cookbook.md) — FastMCP, A2A, BYOT, firewall + drilldown
- [`examples/langchain_memory_demo.py`](https://github.com/dgenio/contextweaver/blob/main/examples/langchain_memory_demo.py)
  — runnable side-by-side LangChain memory comparison
- [LangChain docs](https://python.langchain.com/) ·
  [LangGraph docs](https://langchain-ai.github.io/langgraph/)
- Tracking issue: [#80](https://github.com/dgenio/contextweaver/issues/80)

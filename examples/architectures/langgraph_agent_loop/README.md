# LangGraph agent loop — reference architecture

> contextweaver running **inside** a LangGraph agent loop, not as a
> replacement for one. LangGraph owns control flow; contextweaver owns
> phase-aware context compilation (route → firewall → answer); tool
> execution stays outside contextweaver.

## Run it

```bash
python examples/architectures/langgraph_agent_loop/main.py
```

(Or `make architectures` / `make example`.)

A captured run of the script lives in [`OUTPUT.md`](OUTPUT.md).

## The boundary (the whole point)

| Concern | Owner |
|---|---|
| Control flow (`route -> execute -> answer`, the per-turn loop) | **LangGraph** `StateGraph` |
| Tool selection bounding (catalog → ChoiceCard shortlist) | **contextweaver** route phase |
| Large tool-result firewalling | **contextweaver** interpret phase |
| Budget-aware prompt with dependency-chain preservation | **contextweaver** answer phase |
| Actually executing a tool | The app / a tool runtime (here: mocked) |

contextweaver never executes a tool and never calls a model — it prepares
context and routes tools. The graph nodes do the orchestration.

## LangGraph is optional

The import is guarded:

```python
try:
    from langgraph.graph import END, START, StateGraph
    _HAS_LANGGRAPH = True
except ImportError:
    _HAS_LANGGRAPH = False
```

When LangGraph is installed (`pip install 'contextweaver[langgraph]'`) the
real `StateGraph` drives the loop. Otherwise an equivalent hand-rolled loop
calls the *same* node functions in the same order. The output is identical
either way — the test suite asserts the two paths agree apart from the
one `agent loop engine:` banner line — so the example runs under a bare
`pip install contextweaver`.

## The scenario

A two-turn ops session. The "model" decision at each node is a deterministic
intent map standing in for an LLM holding the rendered ChoiceCards in its
prompt (the comments mark exactly where a real LLM call would go), so the
run needs no API key and no network.

1. *"Our checkout API is throwing 500s — pull the recent error logs for the
   payments service"* → routes to `infra.logs_search`. The tool returns a
   ~21 KB log dump, which the firewall compacts to a short summary while the
   raw bytes stay in the artifact store.
2. *"Summarize the likely root cause from those logs and draft an incident
   note"* → routes to `incident.draft_note`. The answer build carries turn 1's
   firewalled result forward (cross-turn retention); the `dependency_closure`
   stage keeps every tool result paired with its originating tool call.

## What's load-bearing

| contextweaver feature | Used | What it does here |
|---|---|---|
| `Router.route(query)` | ✅ | Narrows 36 tools → top-5 shortlist each turn |
| `ChoiceCard` rendering | ✅ | The shortlist an LLM node would choose from |
| **Context firewall** | ✅✅ | Compacts the ~21 KB log dump before it touches the prompt |
| Artifact store | ✅ | Raw log bytes stay addressable for drilldown |
| Cross-turn `ContextManager` | ✅ | Turn 2 sees turn 1's (firewalled) result |
| `ContextBudget` | ✅ | Keeps every phase prompt bounded |

## What's intentionally not here

- **A real LLM.** The intent map is the stand-in; swap in a model call at
  the marked spot in `route_node`.
- **Real observability tools.** `infra.logs_search` returns a canned dump to
  keep the run deterministic; wire it to your logging backend or an MCP
  server in production.
- **LangGraph persistence/checkpointing.** Cross-turn state lives in the
  `ContextManager`, which is the boundary this example illustrates.

## Read next

- [`docs/architectures/langgraph_agent_loop.md`](../../../docs/architectures/langgraph_agent_loop.md)
  is the public-docs version of this README.
- The [comparison page](../../../docs/comparison.md) explains why
  contextweaver complements (rather than replaces) agent frameworks.

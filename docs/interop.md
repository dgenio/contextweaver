# How contextweaver Fits

> Where does contextweaver sit relative to my agent runtime, my LLM provider,
> and my tool catalog? What does it own, and what does it deliberately leave
> to the rest of the stack? This page is the orientation map.
>
> **In a hurry?** [Which pattern fits my use case?](which_pattern.md) is a
> symptom-based decision tree that lands each branch on one concrete next step.

## Positioning — policy vs. execution

**contextweaver is a policy layer.** It decides _what tools to expose_, _what
context to compile_, and _what to compact_. It never executes tools, never
calls LLMs, and never carries transport or auth.

**Runtimes are the execution layer.** [FastMCP](https://gofastmcp.com/),
[LangChain](https://python.langchain.com/), [LangGraph](https://langchain-ai.github.io/langgraph/),
[LlamaIndex](https://docs.llamaindex.ai/), the [OpenAI Agents SDK](https://platform.openai.com/docs/guides/agents),
[Google's ADK / Vertex AI Agent Builder](https://cloud.google.com/vertex-ai/docs/agent-builder),
and [Pipecat](https://docs.pipecat.ai/) all sit on the outside, driving the
agent loop, invoking tools, and talking to model providers. contextweaver
sits _inside_ that loop and is composed in, not composed over.

## Boundary diagram

```text
┌──────────────────────────────────────────────────────────────┐
│ Runtime (FastMCP / LangChain / LangGraph / LlamaIndex / ADK /│
│          Pipecat / your custom loop)                          │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ contextweaver — policy layer                           │  │
│  │                                                        │  │
│  │   Router.route(query)            → RouteResult         │  │
│  │     └─ ContextManager.build_route_prompt → ChoiceCards │  │
│  │   ContextManager.build(phase, q) → ContextPack         │  │
│  │   Firewall (in ingest_tool_result) → summarised events │  │
│  │                                                        │  │
│  └────────────────────────────────────────────────────────┘  │
│                       ↕ plain callables / protocol objects   │
│                                                              │
│  Tool execution, LLM calls, transport, auth, streaming       │
└──────────────────────────────────────────────────────────────┘
```

The runtime hands events to contextweaver and receives back compact
`RouteResult`s, `ContextPack`s, and (when assembled via
`ContextManager.build_route_prompt()` /
`contextweaver.routing.cards.make_choice_cards()`) `ChoiceCard`s.
contextweaver never reaches outward.

## Interop matrix

| Runtime | Hook type | contextweaver component used | Guide / example | Status |
|---|---|---|---|---|
| MCP servers | Adapter (dict → `SelectableItem`) | `adapters.mcp` + Router + ContextManager | [MCP Integration](integration_mcp.md), `examples/mcp_adapter_demo.py` | Available |
| A2A peers | Adapter (agent card → `SelectableItem`) | `adapters.a2a` + Router + ContextManager | [A2A Integration](integration_a2a.md), `examples/a2a_adapter_demo.py` | Available |
| FastMCP | Adapter (FastMCP tool list → `Catalog`) | `adapters.fastmcp` + Router | [FastMCP cookbook recipe](cookbook.md#1-fastmcp-contextweaver-routing), `examples/fastmcp_adapter_demo.py` | Available |
| FastMCP CodeMode | Custom discovery tool | Router | [FastMCP CodeMode adapter](https://github.com/dgenio/contextweaver/issues/87) | Planned ([#87](https://github.com/dgenio/contextweaver/issues/87)) |
| LlamaIndex | Agent subclass / tool callback | ContextManager + Router | [LlamaIndex Integration](integration_llamaindex.md) | Available |
| LangChain | Callback handler / memory replacement | ContextManager (+ Router) | [LangChain + LangGraph Integration](integration_langchain.md), `examples/langchain_memory_demo.py` | Available |
| LangGraph | State node | ContextManager + Router | [LangChain + LangGraph Integration](integration_langchain.md) | Available |
| OpenAI Agents SDK | Function wrapper / pre-call hook | Router + Firewall | [OpenAI ADK Integration](integration_openai_adk.md) | Available |
| Google ADK / Vertex AI | Tool list filter / pre-call hook | Router + Firewall | [Google ADK Integration](integration_google_adk.md) | Available |
| Pipecat | Frame processor | ContextManager (async) | [Pipecat Integration](integration_pipecat.md) | Available |
| MCP Proxy / Gateway | Standalone server | Full pipeline | — | Planned ([#13](https://github.com/dgenio/contextweaver/issues/13), [#28](https://github.com/dgenio/contextweaver/issues/28), v1.0) |

If your runtime is not listed, the
[bring-your-own-tools cookbook recipe](cookbook.md#3-bring-your-own-tools)
is the canonical pattern — register Python callables as `SelectableItem`s
and drive the loop yourself.

## Minimal integration patterns

You do not have to adopt every contextweaver feature at once. Each of these
patterns is independently useful and composes cleanly with the others.

### Just routing — bounded tool shortlists

```python
from contextweaver.routing.catalog import Catalog
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder

catalog = Catalog()
for item in my_tools:
    catalog.register(item)

graph = TreeBuilder().build(catalog.all())
router = Router(graph, items=catalog.all(), top_k=5)
result = router.route("send a reminder email about unpaid invoices")
shortlist_ids = result.candidate_ids   # feed these into your existing loop
```

### Just firewall — keep large tool outputs out of the prompt

```python
from contextweaver.context.manager import ContextManager

mgr = ContextManager()
item, envelope = mgr.ingest_tool_result_sync(
    tool_call_id="tc-001",
    raw_output=large_tool_response_text,
    tool_name="search_database",
    firewall_threshold=2000,
)
# item.text now holds a compact summary;
# the raw bytes live in mgr.artifact_store under item.artifact_ref.handle.
```

### Full pipeline — phase-specific compiled context

```python
from contextweaver.context.manager import ContextManager
from contextweaver.types import ContextItem, ItemKind, Phase

mgr = ContextManager()
mgr.ingest_sync(ContextItem(id="u1", kind=ItemKind.user_turn, text="user query"))
# ... (tool call / tool result ingestion) ...
pack = mgr.build_sync(phase=Phase.answer, query="user query")
prompt = pack.prompt          # send to your LLM
stats = pack.stats            # BuildStats — what was kept, dropped, why
```

## Non-goals

These are deliberate scope boundaries. Use your runtime for them.

- **Tool execution.** contextweaver never invokes a tool. Your runtime — or
  your own code — calls the tool and feeds the raw output back via
  `ingest_tool_result()`.
- **LLM orchestration.** contextweaver does not call models, manage
  function-calling loops, or stream tokens. You drive the loop; we hand you
  a budgeted prompt.
- **Transport and authentication.** MCP transports, HTTP, gRPC, OAuth,
  service accounts — all belong to the runtime. contextweaver consumes
  already-authenticated tool definitions and results.
- **Schema validation.** contextweaver preserves `args_schema` /
  `output_schema` on `SelectableItem` and `ResultEnvelope` but does not
  validate them. Use the runtime's validator (or `pydantic` /
  `jsonschema`) before invoking a tool.
- **Persistence.** Stores are pluggable, and v0.3 ships only in-memory
  implementations. Durable stores (SQLite, Redis, S3) are on the roadmap
  ([#41](https://github.com/dgenio/contextweaver/issues/41),
  [#42](https://github.com/dgenio/contextweaver/issues/42),
  [#174](https://github.com/dgenio/contextweaver/issues/174)); until then,
  persist `event_log.to_dict()` and friends yourself.

## See also

- [Cookbook](cookbook.md) — copy-paste recipes for FastMCP, A2A,
  bring-your-own-tools, and firewall + drilldown
- [MCP Integration](integration_mcp.md) ·
  [A2A Integration](integration_a2a.md)
- [LlamaIndex](integration_llamaindex.md) ·
  [LangChain + LangGraph](integration_langchain.md) ·
  [OpenAI ADK](integration_openai_adk.md) ·
  [Google ADK](integration_google_adk.md) ·
  [Pipecat](integration_pipecat.md)
- Tracking: [Interop / Policy-Engine positioning epic
  (#86)](https://github.com/dgenio/contextweaver/issues/86)
- Discussion that motivated the boundary framing:
  [PrefectHQ/fastmcp#3365](https://github.com/PrefectHQ/fastmcp/discussions/3365)

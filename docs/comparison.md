# Where contextweaver fits (and where it doesn't)

> A short, honest map of the agent-stack landscape and where contextweaver
> sits in it. If you arrived from a launch post, this is the page that
> answers *"do I need this in addition to what I already use?"*

contextweaver is **a context-compilation layer**: it takes events (user
turns, tool calls, tool results, facts, episodes) and tools (a catalog of
`SelectableItem`s) and produces a *bounded, phase-specific prompt*. It is
not an agent framework, not a memory database, not an LLM SDK, and not a
generic RAG library. The point of this page is to make those boundaries
concrete.

For an adopter-facing decision matrix with more public-positioning language,
see the [Ecosystem Map](ecosystem.md). This page stays closer to the technical
boundaries between layers.

## TL;DR

```text
                     ┌──────────────────────────────────────────────┐
                     │   Your agent loop (LangGraph / CrewAI /      │
                     │   Pydantic AI / your own code)               │
                     │                                              │
                     │   ┌──────────────────────────────────────┐   │
                     │   │   contextweaver                      │   │
                     │   │   • Router → ChoiceCards             │   │
                     │   │   • ContextManager → ContextPack     │   │
                     │   │   • Firewall → ArtifactRef           │   │
                     │   └──────────────────────────────────────┘   │
                     │                                              │
                     │   Tools: MCP servers / FastMCP / plain Python│
                     │   Memory: Mem0 / Zep / LangMem / your store  │
                     │   Retrieval: LlamaIndex / your vector DB     │
                     │   Model: OpenAI / Anthropic / Gemini SDK     │
                     └──────────────────────────────────────────────┘
```

contextweaver is a thin layer **inside** the agent loop. It does not run
the loop and does not call the LLM. It assembles the prompt the loop
gives to the LLM.

## Per-category comparison

### Agent frameworks

| Question | Answer |
|---|---|
| Does contextweaver replace an agent framework? | **No.** |
| Should I keep using my agent framework? | **Yes.** |
| What's the relationship? | contextweaver runs *inside* the agent loop your framework defines. The framework owns the control flow (when to call which tool, when to stop, how to retry); contextweaver owns prompt assembly (which events / tools / facts / summaries enter the prompt at each phase). |

If you do not yet have an agent framework, start with one that fits your
shape (LangGraph for graphs of tool calls, CrewAI for multi-agent flows,
Pydantic AI for typed agents). contextweaver does not opine on which.
See the [LangChain](integration_langchain.md), [OpenAI Agents
SDK](integration_openai_adk.md), [Google ADK](integration_google_adk.md),
[Pipecat](integration_pipecat.md), and [A2A](integration_a2a.md)
integration guides for wiring patterns.

### Memory systems

| Question | Answer |
|---|---|
| Does contextweaver replace a memory database? | **No** — it does not persist memory across sessions out of the box. |
| Does it have facts / episodes? | **Yes**, in-session: `ContextManager.add_fact_sync(...)` and `add_episode_sync(...)`, backed by an append-only `EventLog`. |
| How do I get cross-session persistence? | Plug a persistent backend behind the `FactStore` / `EpisodicStore` / `EventLog` protocols. SQLite-backed `EventLog` / `ArtifactStore` ship today; first-class **Mem0 / Zep / LangMem** adapters ship under `contextweaver.extras.memory` (issue #195). |

contextweaver's stores are protocol-based interfaces
(`contextweaver.store.protocols`), not concrete backends. The two layers are
**complementary**: an external memory system persists facts/episodes across
sessions; contextweaver compiles whatever it surfaces — plus the current
turn's tool calls and results — into a phase-budgeted prompt every turn. It
does *not* replace your memory system; it sits in front of it.

#### Which backend fits

The three shipped backends have different recall shapes — pick the one whose
semantics you want; contextweaver's pipeline is unchanged either way. They
solve different problems, so this is a fit question, not a ranking.

| You want… | Backend | Install | Notes |
|---|---|---|---|
| Passive memory extraction from conversations; multi-tenant scoping | **Mem0** | `pip install 'contextweaver[mem0]'` | Vector + reranker recall; scope by `user_id`. |
| A temporal knowledge graph / time-aware facts | **Zep / Graphiti** | `pip install 'contextweaver[zep]'` | Episodes are the lossless record; scope by `user_id`. |
| LangGraph-native long-term memory shared across threads | **LangMem** | `pip install 'contextweaver[langmem]'` | Wraps any LangGraph `BaseStore`; scope by namespace tuple. |
| Just durable local storage, no external service | **Custom store** | built-in / SQLite | Implement the protocol yourself (below) or use the SQLite `EventLog`. |

#### The plug-in shape

Any object matching the `FactStore` / `EpisodicStore` protocol drops straight
into a `StoreBundle` — no pipeline changes:

```python
from contextweaver.context.manager import ContextManager
from contextweaver.store import StoreBundle
from contextweaver.store.facts import Fact

class MyFactStore:                       # conforms to contextweaver.store.protocols.FactStore
    def put(self, fact: Fact) -> None: ...
    def get(self, fact_id: str) -> Fact: ...
    def get_by_key(self, key: str) -> list[Fact]: ...
    def list_keys(self, prefix: str = "") -> list[str]: ...
    def delete(self, fact_id: str) -> None: ...
    def all(self) -> list[Fact]: ...

ctx_mgr = ContextManager(stores=StoreBundle(fact_store=MyFactStore()))
```

See the [External Memory Backends](integration_memory.md) guide for the full
decision matrix and per-backend wiring, [`faq.md`](faq.md) for the short
answer, and `concepts.md → FactStore / EpisodicStore` for the protocol
definitions. Tracker for first-class integrations: issue
[#195](https://github.com/dgenio/contextweaver/issues/195).

### RAG / vector retrieval

| Question | Answer |
|---|---|
| Is contextweaver RAG? | **No.** RAG retrieves *documents* relevant to a question. contextweaver compiles *agent-loop events and tools* into a phase-budgeted prompt. |
| Can I combine them? | **Yes.** Run RAG to retrieve documents, ingest the retrieved chunks as `ContextItem(kind=doc_snippet, ...)`, and let contextweaver score them against the current query under the same budget pressure as everything else. |
| Does contextweaver ship embeddings? | **No.** The default scorer is lexical (`tfidf` / `bm25`); an embedding-based retrieval backend is tracked under [#8](https://github.com/dgenio/contextweaver/issues/8). |

The shape of the work is different: RAG produces a *list of relevant
documents*; contextweaver produces *the exact prompt the LLM sees*, which
may include some, all, or none of those documents depending on how they
score against the current query, the budget, and what else is competing
for the budget that turn.

### MCP servers and gateways

| Question | Answer |
|---|---|
| Is contextweaver an MCP server? | **No.** |
| Is it an MCP gateway? | **It can be.** `contextweaver.adapters.ProxyRuntime` implements the gateway shape (`tool_browse` / `tool_execute` / `tool_view` meta-tools per [`gateway_spec.md`](gateway_spec.md)) and runs in front of upstream MCP servers. |
| Does it speak the MCP wire protocol? | The runtime accepts and produces MCP-shaped tool defs and tool results. The stdio transport itself is provided by `mcp_gateway_server.py` / `mcp_proxy_server.py` (or by the `mcp` Python SDK). |
| Can it sit beside FastMCP? | **Yes.** Use FastMCP for tool discovery and execution upstream; use contextweaver in front to bound which tool cards reach the agent and to firewall large results. See [`examples/fastmcp_adapter_demo.py`](https://github.com/dgenio/contextweaver/blob/main/examples/fastmcp_adapter_demo.py) and the [MCP integration guide](integration_mcp.md). |

In production, MCP servers expose your tools; contextweaver decides
*which* tool cards the agent sees this turn and *how* their results are
summarised before the next turn.

### Prompt templates

| Question | Answer |
|---|---|
| Does contextweaver replace prompt templates? | **No.** It produces a *block of text* (the `ContextPack.prompt`) you splice into your existing template. |
| Where does the template wrap the contextweaver output? | Typically: `system: <your instructions>\n\n<contextweaver-produced events block>\n\nuser: <current turn>`. The events block is what contextweaver assembles. |
| Can I render contextweaver output myself? | **Yes.** A `ContextPack` exposes `.events` (typed) and `.prompt` (rendered string). Use the typed list with your own template if the default `render_context` output doesn't match your prompt shape. |

### Observability tools

| Question | Answer |
|---|---|
| Is contextweaver observability? | **No** — it is observable. |
| What does it emit? | Every build produces a [`BuildStats`](https://github.com/dgenio/contextweaver/blob/main/src/contextweaver/envelope.py) record: which events were considered, included, dropped, deduplicated, the reasons, the per-section token breakdown, the budget utilisation. |
| OpenTelemetry support? | First-class — see [Observability](integration_otel.md). Emits GenAI semantic-convention attributes (`gen_ai.operation.name`, `gen_ai.client.token.usage`, …) so existing OTel-aware dashboards (LangSmith, Honeycomb, Datadog, Grafana) light up without custom mappers. |
| What does it *not* observe? | Anything outside contextweaver itself: model latency, model cost, downstream user satisfaction, business KPIs. Those belong in your existing observability stack. |

## Interop matrix (one-line summary)

| Layer | Best-in-class | contextweaver's role |
|---|---|---|
| Agent control flow | LangGraph, CrewAI, Pydantic AI, OpenAI Agents SDK | Runs inside their loop |
| Tool execution | MCP servers, FastMCP, plain Python | Reads their tool defs; firewalls their results |
| Memory persistence | Mem0, Zep, LangMem | Stores plug behind contextweaver's protocols |
| Document retrieval | LlamaIndex, vector DBs | Ingests retrieved docs as `ContextItem`s |
| LLM SDK | OpenAI / Anthropic / Gemini official | Hands them the assembled prompt; never calls itself |
| Tracing / metrics | OpenTelemetry-aware dashboards | Emits OTel attributes; doesn't ship its own dashboard |
| Prompt templating | Your framework or plain strings | Produces a text block your template wraps |

## What contextweaver claims (and doesn't)

**It claims:**

- Compile a phase-specific, budget-aware prompt from events and tools.
- Bound the tool catalog to a small `ChoiceCard` shortlist at the route
  phase so the model never sees the whole catalog.
- Run a context firewall so large tool results never reach the prompt raw.
- Preserve dependency chains (`parent_id`) so tool calls and their results
  stay paired.
- Deterministic, network-free, LLM-free in core paths — every benchmark
  number ships with the harness that produces it.

**It does not claim:**

- To replace your agent framework, your model SDK, your memory system, or
  your retrieval stack.
- To "make agents X % cheaper" or "solve tool selection at scale" — the
  [benchmark scorecard](benchmarks.md) reports specific scenarios under
  specific configurations, and the [Known limits](benchmarks.md#known-limits-and-honest-framing)
  section documents where the default scorers degrade.
- To improve answer quality. Better prompts can; that depends on the
  model and the agent loop. contextweaver gives you the prompt; the loop
  uses it.

## See also

- [Showcase](showcase.md) — four runnable demos that exercise the above
  primitives in under a minute each.
- [Ecosystem Map](ecosystem.md) — adopter-facing comparison and decision
  matrix for agent frameworks, MCP, memory, RAG, and observability.
- [Which pattern fits?](which_pattern.md) — symptom-driven routing into
  the right contextweaver primitive.
- [How contextweaver fits (deeper)](interop.md) — policy-vs-execution
  framing, boundary diagram, minimal integration patterns.
- [FAQ](faq.md) — the most common positioning questions.
- [Benchmark scorecard](https://github.com/dgenio/contextweaver/blob/main/benchmarks/scorecard.md) —
  the measured numbers behind the claims above.

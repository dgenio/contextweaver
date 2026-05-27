# Ecosystem Map

> contextweaver is a runtime context-compilation layer. It sits inside an
> existing agent loop and decides what context the model sees this turn.

This page is the adopter-facing version of [Where contextweaver fits](comparison.md).
The short version: keep your framework, MCP servers, memory system, retriever,
model SDK, and observability stack. Add contextweaver when the prompt itself
needs bounded, deterministic context control.

## Decision Matrix

| If you are evaluating... | contextweaver relationship | You probably need contextweaver when... |
|---|---|---|
| Agent frameworks | Complementary | The framework runs the loop, but prompt assembly is still too large or too manual. |
| MCP / FastMCP | Complementary | The server exposes many tools or returns large payloads that should not all reach the model. |
| Memory systems | Complementary | Long-lived memory exists, but each turn still needs budgeted selection. |
| RAG / vector retrieval | Complementary | Retrieved docs compete with tool results, facts, and history for the same prompt budget. |
| Observability | Instrumented by it | You need diagnostics for prompt construction, not another dashboard. |
| LLM SDKs | Upstream consumer | You want a `ContextPack.prompt` to hand to OpenAI, Anthropic, Gemini, or local models. |

| You probably do not need contextweaver if... | Better first step |
|---|---|
| You have not built an agent loop yet. | Pick an agent framework or write the loop first. |
| You only need document retrieval. | Start with RAG or a vector database. |
| You only need durable user memory. | Start with Mem0, Zep, LangMem, or your own memory store. |
| You only need traces, dashboards, or cost reporting. | Start with OpenTelemetry or your existing observability stack. |
| Your tool catalog and tool outputs are tiny. | Keep the simple prompt path until context pressure appears. |

## Agent Frameworks

Frameworks such as LangGraph, CrewAI, Pydantic AI, OpenAI Agents SDK, Google
ADK, and custom loops own control flow:

- when to call the model
- when to call a tool
- how to retry
- when the task is finished

contextweaver owns a narrower question:

```text
Given the current phase and budget, which events, facts, tool cards, and tool
results should enter the prompt?
```

Use both when your framework has a working loop but still leaves you hand-
rolling truncation, tool shortlist prompts, or large-result summaries.

Concrete paths:

| Runtime | Start here |
|---|---|
| LangChain / LangGraph | [LangChain + LangGraph guide](integration_langchain.md) |
| OpenAI Agents SDK | [OpenAI Agents SDK guide](integration_openai_adk.md) |
| Google ADK / Vertex AI | [Google ADK guide](integration_google_adk.md) |
| CrewAI | [CrewAI guide](integration_crewai.md) |
| Pipecat | [Pipecat guide](integration_pipecat.md) |

## MCP and FastMCP

MCP exposes tools and data. It does not decide which of 60, 600, or 6,000 tool
definitions should be shown to a model on a specific turn, and it does not make
large tool results prompt-safe by itself.

contextweaver can sit in front of MCP-style tools in two ways:

| Pattern | What happens |
|---|---|
| Adapter + routing-only | Convert tool definitions to `SelectableItem`s, then route to a small `ChoiceCard` set. |
| Gateway/proxy runtime | Expose `tool_browse`, `tool_execute`, and `tool_view` meta-tools over MCP-shaped payloads. |

Start with the [MCP integration guide](integration_mcp.md), the
[FastMCP cookbook recipe](cookbook.md), or the
[MCP Context Gateway architecture](architectures/mcp_context_gateway.md).

## Memory Systems

Memory systems persist knowledge across sessions. contextweaver decides what
from memory, history, facts, and tool results earns space in the current
prompt.

| System | Primary job | contextweaver job |
|---|---|---|
| Mem0 | Extract and retrieve durable memories. | Budget retrieved memories alongside current events. |
| Zep / Graphiti | Maintain temporal knowledge graphs. | Pull relevant facts into a phase-specific prompt. |
| LangMem | Structure semantic, episodic, and procedural memory for LangGraph. | Compile selected memories with tool context and dependency chains. |
| SQLite / custom stores | Persist events and artifacts. | Query, score, filter, firewall, and render. |

See [External Memory Backends](integration_memory.md) for the protocol shape.
Issue [#195](https://github.com/dgenio/contextweaver/issues/195) tracks the
remaining first-class memory adapters.

## RAG and Vector Retrieval

RAG retrieves documents. contextweaver compiles agent context.

They compose cleanly:

1. Run RAG against your document corpus.
2. Ingest retrieved chunks as `ContextItem(kind=ItemKind.doc_snippet, ...)`.
3. Let contextweaver score those chunks against facts, tool results, and recent
   history under the same token budget.

This is useful when retrieved documents are not the only thing the model needs.
For example, an answer may need a document chunk, the tool result that produced
an incident ID, and the parent tool call that explains where that result came
from.

## Observability

contextweaver is not an observability product. It does emit useful diagnostics:

| Diagnostic | What it explains |
|---|---|
| `BuildStats` | Included, dropped, deduplicated, and token usage counts. |
| Route traces | How routing expanded the graph and ranked candidates. |
| Context explanations | Per-candidate scoring and drop reasons when requested. |
| OpenTelemetry integration | GenAI semantic-convention attributes for existing backends. |

Use [OpenTelemetry](integration_otel.md) or your existing dashboard stack to
store and visualize these signals across production traffic.

## Positioning Boundaries

contextweaver claims:

- bounded tool-card routing
- artifact-backed context firewalling
- phase-specific prompt compilation
- deterministic, LLM-free core execution
- inspectable build and routing diagnostics

contextweaver does not claim:

- to replace your framework, MCP servers, memory layer, RAG stack, model SDK, or
  observability system
- to guarantee answer-quality improvements
- to guarantee a fixed cost reduction for every workload
- to make lexical retrieval sufficient for every large catalog

## See Also

- [Which pattern fits?](which_pattern.md) - symptom-driven adoption paths.
- [Adopter Benchmark Report](benchmark_report.md) - prompt-size, cost, latency,
  and failure-mode framing.
- [FAQ](faq.md) - shorter answers to common positioning questions.
- [Showcase](showcase.md) - deterministic demos of routing, firewalling, long
  history, and MCP gateway patterns.

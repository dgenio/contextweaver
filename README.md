# contextweaver

> Phase-specific, budget-aware context compilation for tool-using AI agents.

**500+ tests passing · zero runtime dependencies · deterministic output · Python ≥ 3.10**

[📖 Documentation](https://dgenio.github.io/contextweaver)

---

## The Problem

Even with 200K-token context windows, dumping everything into the prompt is expensive,
slow, and degrades output quality. More context ≠ better answers.

Imagine a tool-using agent with a 100-tool catalog and a 50-turn conversation history.
At each step the agent must answer four questions:

1. **Route** — which tool should I call?
2. **Call** — what arguments?
3. **Interpret** — what did it return?
4. **Answer** — how do I respond to the user?

**Naive approach A — concatenate everything:**

```
100 tool schemas (≈50k tokens) + 50 turns (≈30k tokens) = 80k tokens
Cost: $0.48/request at GPT-4o rates  ·  Latency: 3–5s TTFT
Quality: LLM loses focus — needle-in-haystack accuracy drops with context size
Token limit: 8k → 10× overflow
```

**Naive approach B — cherry-pick manually:**

```
Pick 10 tools, last 5 turns → lose dependency chains
Agent hallucinates tool calls, repeats questions, forgets context
```

**contextweaver approach — phase-specific budgeted compilation:**

```
Route phase:  5 tool cards (≈500 tokens), no full schemas
Answer phase: 3 relevant turns + dependency closure (≈2k tokens)
Result:       2.5k tokens, complete context, deterministic
Cost:         70% lower  ·  Latency: sub-second  ·  Quality: relevant context only
```

See [`examples/before_after.py`](examples/before_after.py) for a runnable side-by-side comparison.

---

## How contextweaver Solves It

contextweaver provides two cooperating engines:

```
                ┌────────────────────────────┐
  Events ──────>│      Context Engine         │──> ContextPack (prompt)
                │  candidates → closure →     │
                │  sensitivity → firewall →   │
                │  score → dedup → select →   │
                │  render                     │
                └────────────────────────────┘
                           ▲ facts / episodes
                ┌──────────┴─────────────────┐
  Tools ───────>│      Routing Engine         │──> ChoiceCards
                │  Catalog → TreeBuilder →    │
                │  ChoiceGraph → Router       │
                └────────────────────────────┘
```

**Context Engine** — eight-stage pipeline:

1. **generate_candidates** — pull phase-relevant events from the log for this request.
2. **dependency_closure** — if a selected item has a `parent_id`, include the parent automatically.
3. **sensitivity_filter** — drop or redact items at or above the configured sensitivity floor.
4. **apply_firewall** — tool results are stored out-of-band; large outputs are summarized/truncated before prompt assembly.
5. **score_candidates** — rank by recency, tag match, kind priority, and token cost.
6. **deduplicate_candidates** — remove near-duplicates using Jaccard similarity.
7. **select_and_pack** — greedily pack highest-scoring items into the phase token budget.
8. **render_context** — assemble final prompt string with `BuildStats` metadata.

**Routing Engine** — four-stage pipeline:

1. **Catalog** — register and manage `SelectableItem` objects.
2. **TreeBuilder** — convert a flat catalog into a bounded `ChoiceGraph` DAG.
3. **Router** — beam-search over the graph; deterministic tie-breaking by ID.
4. **ChoiceCards** — compact, LLM-friendly cards (never includes full schemas).

---

## Quickstart

### Install

```bash
pip install contextweaver
```

Or from source:

```bash
git clone https://github.com/dgenio/contextweaver.git
cd contextweaver
pip install -e ".[dev]"
```

## 10-Minute Quickstart

For a guided setup with prerequisites, three runnable examples, expected output,
and next steps, see [docs/quickstart.md](docs/quickstart.md).

### Minimal agent loop

```python
from contextweaver.context.manager import ContextManager
from contextweaver.types import ContextItem, ItemKind, Phase

mgr = ContextManager()
mgr.ingest(ContextItem(id="u1", kind=ItemKind.user_turn, text="How many users?"))
mgr.ingest(ContextItem(id="tc1", kind=ItemKind.tool_call,
                       text="db_query('SELECT COUNT(*) FROM users')", parent_id="u1"))
mgr.ingest(ContextItem(id="tr1", kind=ItemKind.tool_result,
                       text="count: 1042", parent_id="tc1"))

pack = mgr.build_sync(phase=Phase.answer, query="user count")
print(pack.prompt)   # budget-aware compiled context
print(pack.stats)    # what was kept, dropped, deduplicated
```

### Route a large tool catalog

```python
from contextweaver.routing.catalog import Catalog, load_catalog_json
from contextweaver.routing.tree import TreeBuilder
from contextweaver.routing.router import Router

catalog = Catalog()
for item in load_catalog_json("catalog.json"):
    catalog.register(item)

graph = TreeBuilder(max_children=10).build(catalog.all())
router = Router(graph, items=catalog.all(), beam_width=3, top_k=5)
result = router.route("send a reminder email about unpaid invoices")
print(result.candidate_ids)
```

## Runtime Loop (4 Phases)

For a complete route -> call -> interpret -> answer reference flow, see:

- `examples/full_agent_loop.py` for a runnable end-to-end script.
- `docs/guide_agent_loop.md` for the flow diagram, pseudo-code, and module map.

The runtime loop example demonstrates:

1. Route-phase prompt assembly with ChoiceCards.
2. Call-phase prompt assembly with selected tool schema hydration.
3. Interpret-phase firewall behavior (large tool output summarized into context).
4. Answer-phase context composition with accumulated history and result envelopes.

---

## Framework Integrations

| Framework | Guide | Use Case |
|---|---|---|
| MCP | [Guide](docs/integration_mcp.md) | Tool conversion, session loading, firewall |
| A2A | [Guide](docs/integration_a2a.md) | Agent cards, multi-agent sessions |
| LlamaIndex | Guide (v0.2) | RAG + tools with budget control |
| OpenAI Agents SDK | Guide (v0.2) | Function-calling agents with routing |
| Google ADK | Guide (v0.2) | Gemini tool-use with context budgets |
| LangChain / LangGraph | Guide (v0.2) | Chain + graph agents with firewall |

---

## Core Concepts

| Concept | Description |
|---|---|
| `ContextItem` | Atomic event log entry: user turn, agent message, tool call, tool result, fact, plan state. |
| `Phase` | `route` / `call` / `interpret` / `answer` — each with its own token budget. |
| `ContextFirewall` | Intercepts tool results: stores raw bytes out-of-band, injects compact summary (with truncation for large outputs). |
| `ChoiceGraph` | Bounded DAG over the tool catalog. Router beam-searches it; LLM sees only a focused shortlist. |
| `ResultEnvelope` | Structured tool output: summary + extracted facts + artifact handles + views. |
| `BuildStats` | Per-build diagnostics: candidate count, included/dropped counts, token usage, drop reasons. |

See [`docs/concepts.md`](docs/concepts.md) for the full glossary and
[`docs/architecture.md`](docs/architecture.md) for pipeline detail and design rationale.

---

## Why Trust contextweaver?

### 1. Test Coverage & Reliability

contextweaver is built for production use with comprehensive quality gates:

- **500+ passing tests** across all modules — context pipeline, routing engine, firewall,
  adapters, stores, CLI, sensitivity enforcement
- **mypy strict** type checking — zero errors across all source files
- **ruff clean** linting — zero warnings
- **CI pipeline** on every pull request and on pushes to `main` ([see workflows](.github/workflows/))
- **Deterministic output** — tie-break by ID, sorted keys; identical inputs always produce
  identical outputs

Run the full suite yourself:

```bash
git clone https://github.com/dgenio/contextweaver.git
cd contextweaver
pip install -e ".[dev]"
make ci  # fmt + lint + type + test + example + demo (all pass)
```

> Most agent libraries fail unpredictably when context exceeds token limits. contextweaver's
> deterministic design and comprehensive test coverage ensure your agent behaves the same way
> every time — critical for debugging, testing, and production deployment.

### 2. Design Rationale

Every architectural choice was made for a reason:

| Decision | Reason |
|---|---|
| **Zero runtime dependencies** | No version conflicts, no supply-chain risks, no bloat. Works in any Python 3.10+ environment. |
| **Protocol-based interfaces** | `EventLog`, `ArtifactStore`, `EpisodicStore`, `FactStore` are `typing.Protocol` — swap backends without forking. |
| **Async-first context engine** | Async-compatible compilation API for real-time integrations; `build_sync()` wrappers for synchronous callers, with room for future non-blocking execution. |
| **Phase-specific token budgets** | Route / call / interpret / answer phases each get their own budget — no one-size-fits-all truncation. |
| **Context firewall** | Large tool outputs stored out-of-band; only compact summaries reach the prompt. |
| **Dependency closure** | `parent_id` chains keep tool results coherent — tool calls are never separated from their results. |

> These aren't accidental features. They are design decisions optimized for reliability,
> extensibility, and production use. Zero dependencies means you can adopt contextweaver
> without disrupting your existing stack.

See [docs/architecture.md](docs/architecture.md) for full pipeline detail and design rationale.

### 3. Standardization via Protocol Support

contextweaver supports both emerging agentic protocols out of the box:

**MCP (Model Context Protocol)** — convert tool definitions and results into native contextweaver types:

- Compatible with any MCP server (Claude Desktop, VS Code, custom servers)
- Structured content, output schemas, binary artifacts, and per-part annotations all handled
- `ingest_mcp_result()` for one-call result ingestion with automatic artifact persistence

**A2A (Agent-to-Agent)** — multi-agent session management with unified context:

- Agent cards converted to `SelectableItem` for routing
- Cross-agent session loading via `load_a2a_session_jsonl()`
- A2A results stored in `ResultEnvelope` with facts and artifact handles

> contextweaver is positioned to become the standard context management layer for AI agents.
> Supporting MCP and A2A now means your codebase is future-proof as these protocols mature
> and gain wider adoption.

- [MCP Integration](docs/integration_mcp.md)
- [A2A Integration](docs/integration_a2a.md)
- [MCP Specification](https://modelcontextprotocol.io/)

### 4. Framework Agnostic

contextweaver works with any LLM provider and any agent framework:

- **LLM providers**: OpenAI, Anthropic, Google, open-source models — no API keys required
  by contextweaver itself
- **Agent frameworks**: LlamaIndex, LangChain, LangGraph, OpenAI Agents SDK, Google ADK,
  Pipecat, custom loops
- **No vendor lock-in**: stdlib-only core; no cloud dependencies; runs anywhere Python 3.10+ runs

<!-- mirrors the Framework Integrations table above; keep in sync -->
| Framework | Guide | Use Case |
|---|---|---|
| MCP | [Guide](docs/integration_mcp.md) | Tool conversion, session loading, firewall |
| A2A | [Guide](docs/integration_a2a.md) | Agent cards, multi-agent sessions |
| LlamaIndex | Guide (v0.2) | RAG + tools with budget control |
| OpenAI Agents SDK | Guide (v0.2) | Function-calling agents with routing |
| Google ADK | Guide (v0.2) | Gemini tool-use with context budgets |
| LangChain / LangGraph | Guide (v0.2) | Chain + graph agents with firewall |

> You are not locked into a specific framework or LLM provider. contextweaver is a layer
> *beneath* frameworks — context management as a composable primitive.

### 5. Versioning & Compatibility

contextweaver follows [Semantic Versioning](https://semver.org/):

- **Breaking changes** to public APIs only in major versions
- **Deprecation policy**: deprecated public APIs are warned for at least one minor version and removed only in a later major release
- **API stability**: public APIs in `contextweaver.*` are stable; internal `_*` modules may change
- **Python support**: 3.10+ (aligned with Python's active security support lifecycle)

| Version | Status | Notes |
|---|---|---|
| **0.1.x** | ✅ Current | Foundation engines (context + routing), MCP/A2A adapters, CLI, sensitivity |
| **0.2.0** | 🚧 In progress (Q2 2026) | Framework integration guides, benchmark suite, distributed stores |
| **0.3.0** | 📋 Planned (Q3 2026) | DAG visualization, merge compression, LLM-assisted labeler |
| **1.0.0** | 📋 Planned (Q4 2026) | API freeze, production benchmarks, enterprise features |

> Adopting a library is a long-term commitment. contextweaver's versioning policy ensures you
> can upgrade safely, and the roadmap shows where it's headed.

### 6. Roadmap & Community

**v0.1 (✅ Complete)**

- Context Engine: 8-stage pipeline (candidates → closure → sensitivity → firewall → score → dedup → select → render)
- Routing Engine: Catalog, DAG builder, beam-search router, choice cards
- Protocol adapters: MCP (full content types, structured content, output schemas) and A2A
- Stores: `EventLog`, `ArtifactStore`, `EpisodicStore`, `FactStore` with protocol-based interfaces
- 500+ passing tests, mypy strict, ruff clean, zero runtime dependencies

**v0.2 (🚧 In Progress — Q2 2026)**

- Framework integration guides: LlamaIndex, LangChain, LangGraph, OpenAI Agents SDK, Google ADK, Pipecat
- Benchmark suite: token reduction, latency, and accuracy vs. naive concatenation
- Distributed stores: Redis-backed `EventLog`, S3-backed `ArtifactStore`

**v0.3 (📋 Planned — Q3 2026)**

- DAG visualization: interactive routing graph inspector
- Merge compression: deduplicate similar tool results across turns
- LLM-based labeler: auto-generate namespace labels for tool catalogs
- LLM-based extractor: structured fact extraction with prompt-based schema

**v1.0 (📋 Planned — Q4 2026)**

- API freeze: no breaking changes in 1.x releases
- Production benchmarks: 1M+ turn deployments
- Enterprise features: audit logging, compliance tags, PII redaction

**Community:**

- [GitHub Discussions](https://github.com/dgenio/contextweaver/discussions) — ask questions, share patterns
- [GitHub Issues](https://github.com/dgenio/contextweaver/issues) — report bugs, request features
- [CHANGELOG](CHANGELOG.md) — track every release

> contextweaver is under active development with a clear roadmap. v0.1 is feature-complete
> for basic use cases; v0.2 adds production-ready integrations; v1.0 is the API stability milestone.

### Comparison

| Approach | Token Control | Tool Routing | Firewall | Framework Agnostic | Dependencies |
|---|---|---|---|---|---|
| **Naive concatenation** | ❌ No | ❌ No | ❌ No | ✅ Yes | None |
| **LangChain ConversationBufferMemory** | ❌ No | ❌ No | ❌ No | ❌ No (LangChain only) | Many |
| **LangChain ConversationSummaryMemory** | ⚠️ LLM-based | ❌ No | ❌ No | ❌ No (LangChain only) | Many |
| **LlamaIndex ContextManager** | ⚠️ Partial | ❌ No | ❌ No | ❌ No (LlamaIndex only) | Many |
| **contextweaver** | ✅ Yes (phase-specific budgets) | ✅ Yes (bounded DAG) | ✅ Yes (out-of-band storage) | ✅ Yes | None |

> Most frameworks offer memory classes, but they don't enforce token budgets, route tools, or
> handle large outputs. contextweaver provides all three as a composable, framework-agnostic layer.

---

## CLI

contextweaver ships with a CLI for quick experimentation:

```bash
contextweaver demo                                    # end-to-end demonstration
contextweaver init                                    # scaffold config + sample catalog
contextweaver build --catalog c.json --out g.json    # build routing graph
contextweaver route --graph g.json --query "send email"
contextweaver print-tree --graph g.json
contextweaver ingest --events session.jsonl --out session.json
contextweaver replay --session session.json --phase answer
```

## Examples

| Script | Description |
|---|---|
| `minimal_loop.py` | Basic event ingestion → context build |
| `full_agent_loop.py` | End-to-end route → call → interpret → answer runtime loop |
| `tool_wrapping.py` | Context firewall in action |
| `routing_demo.py` | Build catalog → route queries → choice cards |
| `before_after.py` | Side-by-side token comparison: WITHOUT vs WITH contextweaver |
| `mcp_adapter_demo.py` | MCP adapter: tool conversion, session loading, firewall |
| `a2a_adapter_demo.py` | A2A adapter: agent cards, multi-agent sessions |
| `langchain_memory_demo.py` | LangChain memory replacement: `InMemoryChatMessageHistory` vs contextweaver |

```bash
make example   # run all examples
```

---

## Development

```bash
make fmt      # format (ruff)
make lint     # lint (ruff)
make type     # type-check (mypy)
make test     # run tests (pytest)
make example  # run all examples
make demo     # run the built-in demo
make ci       # all of the above
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions.

---

## Roadmap

| Milestone | Status | Highlights |
|---|---|---|
| **v0.1 — Foundation** | ✅ complete | Context Engine, Routing Engine, MCP + A2A adapters, CLI, sensitivity enforcement, logging |
| **v0.2 — Integrations** | 🚧 in progress | Framework integration guides (LlamaIndex, OpenAI Agents SDK, Google ADK, LangChain) |
| **v0.3 — Tooling** | 📋 planned | DAG visualization, merge compression, LLM-assisted labeler |
| **Future** | 📋 planned | Context versioning, distributed stores, multi-agent coordination |

See [CHANGELOG.md](CHANGELOG.md) for the detailed release history.

---

## License

Apache-2.0

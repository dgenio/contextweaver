# contextweaver

[![CI](https://github.com/dgenio/contextweaver/actions/workflows/ci.yml/badge.svg)](https://github.com/dgenio/contextweaver/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/contextweaver.svg)](https://pypi.org/project/contextweaver/)
[![Python versions](https://img.shields.io/pypi/pyversions/contextweaver.svg)](https://pypi.org/project/contextweaver/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-mkdocs--material-blue.svg)](https://dgenio.github.io/contextweaver)
[![GitHub Discussions](https://img.shields.io/github/discussions/dgenio/contextweaver)](https://github.com/dgenio/contextweaver/discussions)

> **A context firewall and tool router for tool-heavy AI agents** — drop it
> in front of your MCP servers; prompts stop drowning in tool schemas and
> raw tool output.
>
> Under the hood: phase-specific, budget-aware **context engineering** —
> context compilation with a context firewall plus bounded-choice tool routing.

**1100+ tests passing · minimal core dependencies · deterministic by default · Python ≥ 3.10**

Install:

```bash
pip install contextweaver
```

[📖 Documentation](https://dgenio.github.io/contextweaver) · [🧭 Which pattern fits my use case?](docs/which_pattern.md) · [📊 Benchmark scorecard](benchmarks/scorecard.md)

---

## The Problem

Even with 200K-token context windows, dumping everything into the prompt is expensive,
slow, and degrades output quality. More context ≠ better answers — **context engineering**
(deciding what the model sees, when, and at what cost) is the lever that actually moves
quality and latency.

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
Cost:         42–75% lower [^naive-baseline]  ·  Latency: sub-second  ·  Quality: relevant context only
```

[^naive-baseline]: Measured against the "concatenate all tool schemas + full
    conversation history" baseline using `tiktoken.cl100k_base` on the four
    committed benchmark scenarios. Range 41.6 %–74.5 %, average 55.8 %.
    Reproducible via `make benchmark-matrix && make scorecard` — see the
    *vs. naïve concat baseline* section of
    [`benchmarks/scorecard.md`](benchmarks/scorecard.md) and the
    methodology in [`scripts/baseline_naive.py`](scripts/baseline_naive.py).

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

`contextweaver` ships with a minimal, opinionated core: `tiktoken`,
`PyYAML`, and `rank-bm25`. These power accurate token budgeting, YAML
catalog/config files, and the default lexical retrieval backend.

Optional capabilities are gated behind extras so the core install stays small:

| Extra | What it adds |
|---|---|
| `contextweaver[cli]` | Rich-formatted CLI rendering (rich) |
| `contextweaver[retrieval]` | Fuzzy lexical matching backend (rapidfuzz) |
| `contextweaver[otel]` | OpenTelemetry tracing + metrics export |
| `contextweaver[ann]` | Approximate-nearest-neighbour backend (reserved) |
| `contextweaver[graph]` | NetworkX-backed graph ops (reserved) |
| `contextweaver[fastmcp]` | FastMCP catalog adapter |
| `contextweaver[langchain]` | LangChain integration helpers |
| `contextweaver[all]` | All optional capabilities |

Or from source:

```bash
git clone https://github.com/dgenio/contextweaver.git
cd contextweaver
pip install -e ".[dev]"
```

### Adopting in 5 lines from an existing OpenAI / Anthropic / Gemini agent

```python
from contextweaver.adapters.openai_messages import from_openai_messages
from contextweaver.context.manager import ContextManager
from contextweaver.types import Phase

mgr = ContextManager()
from_openai_messages(messages, into=mgr)   # also: from_anthropic_messages / from_gemini_contents
pack = mgr.build_sync(phase=Phase.answer, query="...")
```

See [Adopting from an existing chat history](docs/quickstart.md#adopting-from-an-existing-chat-history-5-line-drop-in)
for the full snippet (including the `to_*` inverse adapters for round-tripping
back into the provider SDK).

## 10-Minute Quickstart

For a guided setup with prerequisites, three runnable examples, expected output,
and next steps, see [docs/quickstart.md](docs/quickstart.md).

**Already have an agent and not sure which piece you need?**
See [Which pattern fits my use case?](docs/which_pattern.md) — a symptom-based
decision tree (long conversations → full pipeline; 50+ tools → routing-only;
huge tool outputs → firewall-only) that points each branch to one concrete
next step.

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

Looking for "where does contextweaver fit alongside my runtime?" — start with the
[How contextweaver Fits](docs/interop.md) positioning page, then jump into the
[Cookbook](docs/cookbook.md) for copy-paste recipes.

| Framework | Guide | Use Case |
|---|---|---|
| MCP | [Guide](docs/integration_mcp.md) | Tool conversion, session loading, firewall · [Security note](docs/integration_mcp.md#security-considerations) |
| A2A | [Guide](docs/integration_a2a.md) | Agent cards, multi-agent sessions |
| FastMCP | [Cookbook recipe](docs/cookbook.md#1-fastmcp--contextweaver-routing) | Composed MCP servers → bounded-choice routing |
| LlamaIndex | [Guide](docs/integration_llamaindex.md) | RAG + tools with budget control |
| OpenAI Agents SDK | [Guide](docs/integration_openai_adk.md) | Swarm hand-offs with unified context |
| Google ADK / Vertex AI | [Guide](docs/integration_google_adk.md) | Gemini tool-use with context budgets |
| LangChain + LangGraph | [Guide](docs/integration_langchain.md) | Chain + graph agents with firewall |
| Pipecat | [Guide](docs/integration_pipecat.md) | Real-time voice agents with async context build |
| CrewAI | [Guide](docs/integration_crewai.md) | Role-based agent crews with bounded tool shortlists |
| External memory (Mem0) | [Guide](docs/integration_memory.md) | Plug an existing Mem0 deployment as the `EpisodicStore` / `FactStore` |

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

See [`docs/concepts.md`](docs/concepts.md) for the full glossary,
[`docs/architecture.md`](docs/architecture.md) for pipeline detail and design rationale,
and [`docs/troubleshooting.md`](docs/troubleshooting.md) for common issues, debugging
techniques, and performance optimisation tips.

---

## Why Trust contextweaver?

### 1. Test Coverage & Reliability

contextweaver is built for production use with comprehensive quality gates:

- **1100+ passing tests** across all modules — context pipeline, routing engine, firewall,
  adapters, stores, CLI, sensitivity enforcement
- **mypy strict** type checking — zero errors across all source files
- **ruff clean** linting — zero warnings
- **CI pipeline** on every pull request and on pushes to `main` ([see workflows](.github/workflows/))
- **Deterministic by default** — tie-break by ID, sorted keys; identical inputs always
  produce identical outputs. Configurable retrieval backends (TF-IDF, BM25, fuzzy)
  preserve determinism within each mode.
- **Public benchmark scorecard** — top-k recall, token savings, and routing latency at
  catalog sizes 50 / 83 / 1000, plus context pipeline metrics across three reference
  scenarios. See [`benchmarks/scorecard.md`](benchmarks/scorecard.md) (regenerate with
  `make scorecard`).

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
| **Minimal core dependencies** | A small, audited set of widely-used deps (`tiktoken`, `PyYAML`, `rank-bm25`, `mcp`, `jsonschema`, `typer`, `rich`); no heavy ML / cloud-SDK packages pulled in by default. |
| **Protocol-based interfaces** | `EventLog`, `ArtifactStore`, `EpisodicStore`, `FactStore` are `typing.Protocol` — swap backends without forking. |
| **Async-first context engine** | Async-compatible compilation API for real-time integrations; `build_sync()` wrappers for synchronous callers, with room for future non-blocking execution. |
| **Phase-specific token budgets** | Route / call / interpret / answer phases each get their own budget — no one-size-fits-all truncation. |
| **Context firewall** | Large tool outputs stored out-of-band; only compact summaries reach the prompt. |
| **Dependency closure** | `parent_id` chains keep tool results coherent — tool calls are never separated from their results. |

> These aren't accidental features. They are design decisions optimized for reliability,
> extensibility, and production use. A minimal, audited core-dependency set means you
> can adopt contextweaver without disrupting your existing stack.

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

**weaver-spec** — canonical contracts for the Weaver Stack (contextweaver,
ChainWeaver, agent-kernel):

- Lossless `to_weaver_*` / `from_weaver_*` round-trips for `SelectableItem`,
  `ChoiceCard`, `RoutingDecision`, and `Frame` (via `ResultEnvelope`)
- `weaver_contracts` is an opt-in dep — `pip install 'contextweaver[weaver-spec]'`
- Validated in CI on every PR against the JSON Schemas at
  `raw.githubusercontent.com/dgenio/weaver-spec/main/contracts/json/`
  (the source the gate fetches; the same documents are also published at
  `https://weaver-spec.dev/contracts/v0/`)

> contextweaver is positioned to become the standard context management layer for AI agents.
> Supporting MCP, A2A, and weaver-spec now means your codebase is future-proof as these
> protocols mature and gain wider adoption.

- [MCP Integration](docs/integration_mcp.md)
- [A2A Integration](docs/integration_a2a.md)
- [weaver-spec mapping](docs/weaver_spec_mapping.md)
- [MCP Specification](https://modelcontextprotocol.io/)
- [weaver-spec](https://github.com/dgenio/weaver-spec)

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
| FastMCP | [Cookbook recipe](docs/cookbook.md#1-fastmcp--contextweaver-routing) | Composed MCP servers → bounded-choice routing |
| LlamaIndex | [Guide](docs/integration_llamaindex.md) | RAG + tools with budget control |
| OpenAI Agents SDK | [Guide](docs/integration_openai_adk.md) | Swarm hand-offs with unified context |
| Google ADK / Vertex AI | [Guide](docs/integration_google_adk.md) | Gemini tool-use with context budgets |
| LangChain + LangGraph | [Guide](docs/integration_langchain.md) | Chain + graph agents with firewall |
| Pipecat | [Guide](docs/integration_pipecat.md) | Real-time voice agents with async context build |
| CrewAI | [Guide](docs/integration_crewai.md) | Role-based agent crews with bounded tool shortlists |
| External memory (Mem0) | [Guide](docs/integration_memory.md) | Plug an existing Mem0 deployment as the `EpisodicStore` / `FactStore` |

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

#### Weaver Spec Compatibility

contextweaver implements `weaver_contracts >= 0.2.0, < 1.0` (canonical
contracts for the Weaver Stack — see
[weaver-spec](https://github.com/dgenio/weaver-spec)).

| Invariant | Status | Where enforced |
|---|---|---|
| **I-03** — Routing presents bounded choices, not full schema catalogs | ✅ Satisfied | `ChoiceCard` strips `args_schema`; routing returns ≤ `top_k` cards. See [`src/contextweaver/routing/cards.py`](src/contextweaver/routing/cards.py) and [`docs/gateway_spec.md`](docs/gateway_spec.md). |
| **I-05** — contextweaver receives Frames, not raw output | ⚠️ Satisfied on the Frame adapter path | The canonical Frame-shaped ingestion lives on the spec adapter: tool outputs above `firewall_threshold` are stored out-of-band as `ArtifactRef`s and the LLM-facing `ResultEnvelope` maps to a spec `Frame` via [`adapters/weaver_contracts.py`](src/contextweaver/adapters/weaver_contracts.py). The legacy raw-output ingestion APIs (`ContextManager.ingest_tool_result(raw_output=...)`, `ingest_mcp_result(...)`) still exist for backwards compatibility; treat them as non-canonical for spec compliance. |

**Contract adapters** (`pip install 'contextweaver[weaver-spec]'`):

```python
from contextweaver.adapters.weaver_contracts import (
    to_weaver_routing_decision,
    from_weaver_routing_decision,
    to_weaver_frame,
    from_weaver_frame,
)
```

Round-trips are lossless via a reserved `metadata["_contextweaver"]` payload;
see [`docs/weaver_spec_mapping.md`](docs/weaver_spec_mapping.md) for the full
mapping table.

**CI conformance** — every PR runs `scripts/weaver_spec_conformance.py`,
which does both a Python round-trip (`cw → spec → cw == cw`) and JSON-Schema
validation. CI fetches the schemas from
`raw.githubusercontent.com/dgenio/weaver-spec/main/contracts/json/`, which
mirrors the published documents at `https://weaver-spec.dev/contracts/v0/`
(same content, different host). Run locally with `make weaver-conformance`.

### 6. Roadmap & Community

**v0.1 (✅ Complete)**

- Context Engine: 8-stage pipeline (candidates → closure → sensitivity → firewall → score → dedup → select → render)
- Routing Engine: Catalog, DAG builder, beam-search router, choice cards
- Protocol adapters: MCP (full content types, structured content, output schemas) and A2A
- Stores: `EventLog`, `ArtifactStore`, `EpisodicStore`, `FactStore` with protocol-based interfaces
- 1100+ passing tests, mypy strict, ruff clean, minimal core dependencies

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
contextweaver stats --session session.json --format text
contextweaver budget-check --session session.json --phase answer --max-tokens 4000
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
| `cookbook/byot_recipe.py` | Bring-your-own-tools cookbook recipe — wrap plain Python callables and route |
| `cookbook/firewall_drilldown_recipe.py` | Cookbook recipe: firewall a large tool result, then drill into the artifact |
| `architectures/slack_ops_bot/` | Production reference architecture — internal Slack ops bot with ~50 tools, firewall on log/grep outputs, persistent facts ([guide](docs/architectures/slack_ops_bot.md)) |

```bash
make example   # run all examples
```

---

## FAQ

**Q: What token budgets should I use?**
Start with the defaults (`route=2000`, `call=3000`, `interpret=4000`, `answer=6000`).
Inspect `pack.stats` after each build and increase any phase that drops too many items.

**Q: My tool result was summarized. Why?**
The context firewall intercepts *every* `tool_result` item (not just large ones).
Raw data is stored out-of-band; access it via `mgr.artifact_store.get("artifact:<item_id>")`.
Provide a custom `Summarizer` to control how the summary is generated.

**Q: How do I debug what was kept or dropped?**
Inspect `pack.stats` (a `BuildStats` object) after every `build_sync()` / `build()` call:
`included_count`, `dropped_count`, `dropped_reasons`, `dedup_removed`.

**Q: Does this work with [framework X]?**
Yes, contextweaver is framework-agnostic — it compiles context; you send `pack.prompt`
to any LLM or framework. See dedicated guides for
[MCP](docs/integration_mcp.md),
[A2A](docs/integration_a2a.md),
[LlamaIndex](docs/integration_llamaindex.md),
[LangChain + LangGraph](docs/integration_langchain.md),
[OpenAI Agents SDK](docs/integration_openai_adk.md),
[Google ADK / Vertex AI](docs/integration_google_adk.md),
[Pipecat](docs/integration_pipecat.md), and
[CrewAI](docs/integration_crewai.md).  Already running a long-lived
memory layer? Adapt
[Mem0](docs/integration_memory.md) onto the `EpisodicStore` / `FactStore`
protocols.  If your runtime isn't listed, the
[bring-your-own-tools cookbook recipe](docs/cookbook.md#3-bring-your-own-tools)
is the canonical starting point.

**Q: What's the performance overhead?**
Typically 10–50 ms for a context build (depends on event log size and deduplication).
For real-time / async agents, run `build_sync()` in a worker thread (e.g.
`await asyncio.to_thread(mgr.build_sync, phase, query)`) so the synchronous
pipeline does not block the event loop.

See [docs/troubleshooting.md](docs/troubleshooting.md) for the full troubleshooting
guide, debugging techniques, optimisation tips, and 10+ common issues with solutions.

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

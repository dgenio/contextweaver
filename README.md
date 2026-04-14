# contextweaver

> Phase-specific, budget-aware context compilation for tool-using AI agents.

**500+ tests passing · zero runtime dependencies · deterministic output · Python ≥ 3.10**

---

## The Problem

Imagine a tool-using agent with a 100-tool catalog and a 50-turn conversation history.
At each step the agent must answer four questions:

1. **Route** — which tool should I call?
2. **Call** — what arguments?
3. **Interpret** — what did it return?
4. **Answer** — how do I respond to the user?

**Naive approach A — concatenate everything:**

```
100 tool schemas (≈50k tokens) + 50 turns (≈30k tokens) = 80k tokens
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

---

## Framework Integrations

| Framework | Guide | Use Case |
|---|---|---|
| MCP | [Guide](docs/integration_mcp.md) | Tool conversion, session loading, firewall |
| A2A | [Guide](docs/integration_a2a.md) | Agent cards, multi-agent sessions |
| LlamaIndex | Guide (coming soon) | RAG + tools with budget control |
| OpenAI Agents SDK | Guide (coming soon) | Function-calling agents with routing |
| Google ADK | Guide (coming soon) | Gemini tool-use with context budgets |
| LangChain / LangGraph | Guide (coming soon) | Chain + graph agents with firewall |

---

## Why Trust contextweaver?

| Proof point | Detail |
|---|---|
| **500+ tests passing** | Context pipeline, routing engine, firewall, adapters, CLI, sensitivity enforcement |
| **Zero runtime dependencies** | Stdlib-only, Python ≥ 3.10. Works with any LLM provider. No vendor lock-in. |
| **Deterministic** | Tie-break by ID, sorted keys. Identical inputs always produce identical outputs. |
| **Protocol-based stores** | `EventLog`, `ArtifactStore`, `EpisodicStore`, `FactStore` are `typing.Protocol` interfaces — swap any backend. |
| **MCP + A2A adapters** | First-class support for both emerging agentic standards. |
| **`BuildStats` transparency** | Every context build reports exactly what was kept, dropped, deduplicated, and why. |

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
| `tool_wrapping.py` | Context firewall in action |
| `routing_demo.py` | Build catalog → route queries → choice cards |
| `before_after.py` | Side-by-side token comparison: WITHOUT vs WITH contextweaver |
| `mcp_adapter_demo.py` | MCP adapter: tool conversion, session loading, firewall |
| `a2a_adapter_demo.py` | A2A adapter: agent cards, multi-agent sessions |

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
to any LLM or framework.
See [integration guides](docs/) for MCP and A2A; LlamaIndex, LangChain, OpenAI Agents
SDK, and Google ADK guides are in progress.

**Q: What's the performance overhead?**
Typically 10–50 ms for a context build (depends on event log size and deduplication).
Use `build()` (async) for real-time agents to avoid blocking the event loop.

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

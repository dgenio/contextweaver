# contextweaver

Dynamic context management for tool-using AI agents.

contextweaver is a zero-dependency Python library that compiles phase-specific,
budget-bounded context for LLMs in agent loops. It replaces naive prompt
concatenation with a structured pipeline that scores, deduplicates, and packs
context items within a token budget -- and keeps large tool outputs out of
the prompt entirely.

```
pip install contextweaver
```

## The Problem

Tool-using AI agents suffer from three interrelated context problems:

**Context bloat.** Every conversation turn, tool call, and tool result gets
concatenated into the prompt. After a few rounds, the prompt exceeds the
context window or drowns relevant information in noise.

**Tool-space interference.** When an agent has 50+ tools, including all tool
descriptions in the prompt wastes tokens and confuses the model. The LLM
hallucinates tool calls to tangentially related tools it should never have
seen.

**Output swamping.** A single database query can return 10,000 rows. A web
scrape can produce 50KB of text. If this goes into the prompt, it consumes
the entire token budget and pushes out the user's actual question.

### Why Not Just Stuff Everything in the Prompt?

Because it fails in predictable ways:

| Failure mode          | Symptom                                            |
|-----------------------|----------------------------------------------------|
| Budget overrun        | Prompt exceeds context window; API error or silent truncation |
| Relevance dilution    | Important facts buried in noise; model ignores them |
| Attention interference| Model attends to irrelevant tool schemas instead of user intent |
| Output swamping       | One large tool result consumes 90% of available tokens |
| Stale context         | Old conversation turns crowd out recent evidence   |

contextweaver addresses all five by treating context as a **compiled
projection** of an append-only event log -- not a raw concatenation of
everything that has happened.

## Mental Model

```
  Event Log (append-only ground truth)
       |
       | compile(goal, phase, budget)
       |
       |   1. Filter by phase (ROUTE/CALL/INTERPRET/ANSWER)
       |   2. Score on recency, relevance, kind priority
       |   3. Deduplicate by content hash
       |   4. Pack greedily within token budget
       |   5. Render into structured prompt sections
       |
       v
  ContextPack
       .rendered_text       What the LLM sees
       .stats               What the engine dropped and why
       .artifacts_available  Handles for drilldown into raw data
```

The event log grows without bound. The ContextPack is always bounded.

## How This Library Fixes It

### Context Engine Assembly (CEA)

The five-stage pipeline:

1. **Candidate generation** -- filter event log items by phase, sensitivity,
   TTL, and redaction hooks
2. **Scoring** -- rank candidates on four weighted axes: recency, tag overlap,
   kind priority, and token cost
3. **Deduplication** -- remove identical items by content hash
4. **Selection** -- greedy budget packing with dependency closure (tool results
   pull in their parent tool calls)
5. **Rendering** -- deterministic section-based output (facts, episodes, items)

### Token Budgets

Each of the four execution phases has its own token budget:

| Phase       | Default budget | Purpose                          |
|-------------|---------------|----------------------------------|
| ROUTE       | 2,000         | Choose which tool/agent to invoke |
| CALL        | 3,000         | Prepare tool invocation arguments |
| INTERPRET   | 4,000         | Process and interpret tool output |
| ANSWER      | 6,000         | Compile the final user response   |

### Context Firewall

Large tool outputs never enter the prompt directly. When a result exceeds
the firewall threshold (default 2,000 chars):

- The raw output is stored in an out-of-band `ArtifactStore`
- A concise summary (~300 chars) goes into the prompt
- Structured facts (entities, keys, statistics) are extracted
- A handle enables drilldown into the full data on demand

### Bounded-Choice Routing

For agents with large tool catalogs, the routing engine:

1. Partitions tools into a bounded tree (max 20 children per node)
2. Beam-searches the tree to find the top-k relevant tools
3. Renders compact choice cards (no schemas, max 240 chars each)

The LLM picks from 5-10 focused choices instead of scanning 100+ tool
descriptions.

## Where It Fits

contextweaver sits between your agent framework and the LLM:

```
  User request
       |
       v
  +---[ Your Agent Framework ]---+
  |                              |
  |   contextweaver              |
  |     .ingest_sync(item)       |   <-- feed events in
  |     .ingest_tool_result_sync |   <-- firewall large outputs
  |     .build_sync(goal, phase) |   <-- get bounded context out
  |     Router.route(query)      |   <-- get tool recommendations
  |                              |
  +------------------------------+
       |
       v
  LLM API (OpenAI, Anthropic, local, ...)
```

Compatible with:
- **MCP** (Model Context Protocol) -- adapters for tool definitions and results
- **A2A** (Agent-to-Agent) -- adapters for agent descriptors and delegation
- **Internal tools** -- any callable that returns text or structured data

## 60-Second Quickstart

### Python API

```python
from contextweaver import ContextManager, ContextItem, ItemKind, Phase

mgr = ContextManager()

# Ingest conversation events
mgr.ingest_sync(ContextItem(
    id="u1", kind=ItemKind.USER_TURN,
    text="How many users signed up last month?",
    token_estimate=9,
))
mgr.ingest_sync(ContextItem(
    id="tc1", kind=ItemKind.TOOL_CALL,
    text='db_query(sql="SELECT COUNT(*) FROM users WHERE created_at >= ...")',
    token_estimate=15, parent_id="u1",
))

# Ingest a large tool result through the firewall
item, envelope = mgr.ingest_tool_result_sync(
    tool_call_id="tc1",
    raw_output=large_query_result,
    tool_name="db_query",
)

# Build phase-specific context
pack = mgr.build_sync(
    goal="Answer user about monthly signups",
    phase=Phase.ANSWER,
)

print(pack.rendered_text)   # Bounded prompt for the LLM
print(pack.stats.to_dict()) # Pipeline telemetry
```

### CLI

```bash
# Run the built-in demo
python -m contextweaver demo

# Build context from a recorded session
python -m contextweaver build session.jsonl --phase answer

# Route a query over a tool catalog
python -m contextweaver route "search for invoices" catalog.json
```

## API Overview

### Core Classes

| Class            | Module                          | Purpose                                |
|------------------|---------------------------------|----------------------------------------|
| `ContextManager` | `contextweaver.context.manager` | Main entry point. Ingest, build, route. |
| `ContextPack`    | `contextweaver.context.manager` | Output of a context build.              |
| `PromptBuilder`  | `contextweaver.context.prompt`  | Build full prompts with choice cards.   |
| `Router`         | `contextweaver.routing.router`  | Beam-search tool routing.               |
| `TreeBuilder`    | `contextweaver.routing.tree`    | Build bounded choice graph from catalog.|
| `ChoiceGraph`    | `contextweaver.routing.graph`   | The routing DAG data structure.         |

### Data Types

| Type              | Purpose                                          |
|-------------------|--------------------------------------------------|
| `ContextItem`     | A single event in the log (user turn, tool result, ...) |
| `SelectableItem`  | A tool, agent, skill, or internal function        |
| `ResultEnvelope`  | Structured wrapper for tool/agent output          |
| `BuildStats`      | Telemetry from the context build pipeline         |
| `ArtifactRef`     | Reference to out-of-band stored data              |
| `ViewSpec`        | Named drilldown projection of an artifact         |
| `ChoiceCard`      | Compact LLM-friendly tool/agent representation    |

### Configuration

| Config class    | Controls                                       |
|-----------------|------------------------------------------------|
| `ContextBudget` | Per-phase token limits                         |
| `ContextPolicy` | Allowed kinds per phase, sensitivity floor, TTL |
| `ScoringConfig` | Weights for recency, tags, kind, cost          |

### Adapters

| Function                 | Source     | Purpose                          |
|--------------------------|------------|----------------------------------|
| `mcp_tool_to_item`       | MCP        | Tool definition -> SelectableItem |
| `mcp_result_to_envelope` | MCP        | Tool result -> ResultEnvelope    |
| `load_mcp_session_jsonl` | MCP        | Session file -> list[ContextItem] |
| `agent_to_item`          | A2A        | Agent card -> SelectableItem     |
| `agent_response_to_envelope` | A2A    | Agent response -> ResultEnvelope |
| `load_a2a_session_jsonl` | A2A        | Session file -> list[ContextItem] |

## Architecture

```
src/contextweaver/
  |
  +-- types.py           Dataclasses & enums (ContextItem, Phase, ...)
  +-- config.py          ContextBudget, ContextPolicy, ScoringConfig
  +-- protocols.py       Extension point protocols (TokenEstimator, ...)
  +-- exceptions.py      BudgetExceededError, ArtifactNotFoundError, ...
  +-- _utils.py          tokenize(), jaccard(), TfIdfScorer
  +-- serde.py           Serialization helpers
  |
  +-- store/
  |     +-- event_log.py     Append-only event log
  |     +-- artifacts.py     Out-of-band artifact store + drilldown
  |     +-- facts.py         Durable key-value fact store
  |     +-- episodic.py      Rolling episodic summaries
  |
  +-- context/
  |     +-- manager.py       ContextManager + ContextPack
  |     +-- candidates.py    Stage 1: candidate generation
  |     +-- scoring.py       Stage 2: multi-axis scoring
  |     +-- dedup.py         Stage 3: content deduplication
  |     +-- selection.py     Stage 4: budget packing
  |     +-- prompt.py        Stage 5: rendering + PromptBuilder
  |     +-- firewall.py      Intercept large outputs
  |
  +-- routing/
  |     +-- catalog.py       Load / generate tool catalogs
  |     +-- graph.py         ChoiceGraph + ChoiceNode
  |     +-- tree.py          TreeBuilder (bounded partitioning)
  |     +-- router.py        Beam-search router
  |     +-- cards.py         Choice card rendering
  |     +-- labeler.py       Auto-labeling for tree nodes
  |
  +-- summarize/
  |     +-- rules.py         Head+tail summarizer
  |     +-- extract.py       Structured fact extraction
  |
  +-- adapters/
  |     +-- mcp.py           MCP protocol conversion
  |     +-- a2a.py           A2A protocol conversion
  |
  +-- __main__.py            CLI entry point
```

## Design Limitations

contextweaver v0.1 is deliberately minimal:

- **Rule-based summarization only.** The `RuleBasedSummarizer` uses
  head+tail truncation and JSON structure detection. It does not call an LLM
  for summarization. This keeps the library zero-dependency but limits
  summary quality for complex outputs.

- **In-memory stores only.** All four stores are in-memory Python dicts.
  There are no persistent or distributed backends yet.

- **No streaming.** The pipeline is batch-oriented: build the full
  ContextPack, then pass it to the LLM. There is no incremental or
  streaming mode.

- **No learned scoring.** The scoring weights are static configuration.
  There is no feedback loop or learned ranking.

- **Single-session scope.** Each `ContextManager` instance manages one
  session. Cross-session context sharing is not built in.

## v0.2 Roadmap

Planned improvements for the next release:

- **Persistent store backends** -- SQLite, Redis, and filesystem adapters
  for the event log and artifact store
- **LLM-assisted summarization** -- optional `LLMSummarizer` that calls a
  small model for higher-quality summaries (behind a protocol, not a hard
  dependency)
- **Streaming context** -- incremental ContextPack updates as new items
  arrive, without rebuilding from scratch
- **Merge compression** -- adjacent items of the same kind sharing a parent
  are merged into a single, compressed item
- **Cross-session facts** -- a persistent fact store that spans sessions,
  with conflict resolution
- **OpenTelemetry integration** -- structured traces for the pipeline stages
- **Prompt caching hints** -- output cache-friendly prefixes so LLM
  providers can reuse cached KV entries across builds

## Examples

All examples run with no external APIs:

```bash
python examples/minimal_loop.py      # Four-phase agent loop
python examples/tool_wrapping.py     # Firewall + artifact drilldown
python examples/mcp_adapter_demo.py  # MCP session loading + context build
python examples/a2a_adapter_demo.py  # Multi-agent session + context build
python examples/before_after.py      # Token savings comparison
python examples/routing_demo.py      # Catalog -> graph -> route -> cards
```

## Development

```bash
pip install -e ".[dev]"
make fmt     # auto-format
make lint    # style checks
make type    # type checks
make test    # test suite
make ci      # all of the above + run examples
```

## License

MIT

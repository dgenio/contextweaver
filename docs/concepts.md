# Concepts

This document explains the core concepts behind contextweaver's design.
Each concept addresses a specific failure mode in naive tool-using agent
systems.

## Context Is a Compiled Projection

In a traditional agent loop, "context" means "everything we have seen so
far, concatenated into a prompt." This creates three problems:

1. **Token bloat** -- the prompt grows linearly with conversation length
2. **Relevance dilution** -- important items are buried in noise
3. **Budget overruns** -- the LLM's context window fills up unpredictably

contextweaver treats context differently. The event log is the ground truth
-- an append-only record of every user turn, agent message, tool call, and
tool result. But the prompt is never the raw log. Instead, context is a
**compiled projection**: a phase-specific, budget-bounded, relevance-scored
subset of the log, rendered into a structured prompt.

```
  Event Log (ground truth, grows forever)
       |
       | compile(goal, phase, budget)
       v
  ContextPack (bounded, phase-specific, scored)
       |
       | render()
       v
  Prompt text (what the LLM actually sees)
```

The compilation step is where contextweaver adds value. It ensures the LLM
sees the most relevant information for its current task, within a predictable
token budget, with full traceability of what was included and what was
dropped.

## Phase-Specific Projection

Not every LLM call in an agent loop needs the same information. contextweaver
defines four phases that correspond to the four types of LLM invocations
in a typical tool-using agent:

| Phase       | Purpose                           | Needs                          |
|-------------|-----------------------------------|--------------------------------|
| **ROUTE**   | Choose which tool/agent to invoke | User intent, plan state, policy |
| **CALL**    | Prepare tool invocation arguments | Recent history, tool schemas   |
| **INTERPRET**| Process the tool's output        | Tool result, call context      |
| **ANSWER**  | Compile the final user response  | All evidence, provenance       |

Each phase has:
- A **token budget** (configurable via `ContextBudget`)
- An **allowed kinds filter** (which `ItemKind` values are eligible)
- **Kind priority weights** (which kinds are scored highest)

This means the ROUTE phase context is small and focused (just user intent
and plan), while the ANSWER phase context is larger and comprehensive (all
evidence from the conversation).

### Why Four Phases?

Four phases is the minimum needed to model the complete tool-use cycle:
decide, prepare, process, respond. Each phase has fundamentally different
information requirements. Collapsing them into one phase wastes tokens.
Splitting further adds complexity without clear benefit.

## Multi-Layer Stores

contextweaver maintains four complementary stores, each serving a different
temporal and semantic role:

```
                   Temporal scope
                   <--- per-turn --- per-session --- persistent --->

  Event Log        [x]   Append-only record of all items
  Artifact Store   [x]   Large raw outputs, keyed by handle
  Episodic Store         [x]   Rolling summaries of conversation segments
  Fact Store                   [x]   Durable semantic facts
```

### Event Log

The canonical record. Every `ContextItem` goes here. The pipeline reads
from here during candidate generation.

### Artifact Store

Out-of-band storage for large payloads that should not be in the prompt.
Supports structured drilldown (head, lines, json_keys, rows).

### Episodic Store

Rolling summaries of conversation segments. Useful for multi-turn
conversations where early turns have been evicted from the event log.
The three most recent episodes are included in every context build.

### Fact Store

Key-value store for durable semantic facts (e.g. "user_name: Alice",
"preferred_language: Python"). Facts are included in every context build
as a "Known Facts" section. Last-write-wins semantics.

## Handles, Views, and Drilldown

When the context firewall intercepts a large tool output, it stores the
raw payload in the `ArtifactStore` and puts only a summary into the
`ContextItem`. But the LLM may need to inspect the raw data. contextweaver
provides three mechanisms:

### Handles

An `artifact_ref` on a `ContextItem` is a string handle pointing to the
full raw data in the artifact store. The LLM can request the full artifact
by handle.

### Views

A `ViewSpec` defines a named, pre-configured projection of an artifact:

```python
ViewSpec(
    view_id="head_art_tc1",
    label="First 500 chars",
    selector={"type": "head", "chars": 500},
    artifact_ref="art_tc1",
)
```

Views are listed in the `ResultEnvelope` so the agent framework knows what
projections are available without fetching the full artifact.

### Drilldown

The `ArtifactStore.drilldown(handle, selector)` method extracts a specific
slice of the artifact:

| Selector type | Parameters       | Example                                |
|---------------|------------------|----------------------------------------|
| `head`        | `chars: int`     | First N characters                     |
| `lines`       | `start, end`     | Line range (like `sed -n '1,10p'`)     |
| `json_keys`   | `keys: list`     | Extract specific keys from JSON object |
| `rows`        | `start, end`     | Row range for JSON arrays / tables     |

This lets an agent framework implement progressive disclosure: start with
the summary, then drill down into specific parts of the raw data as needed.

## Context Firewall and Structured Extraction

### The Problem: Output Swamping

Tool outputs can be enormous. A database query might return 10,000 rows.
A web scrape might produce 50KB of HTML. If this goes directly into the
prompt, it:
- Consumes most of the token budget
- Pushes out earlier, more important context
- Contains mostly irrelevant data

### The Solution: Context Firewall

When `ingest_tool_result` is called and the output exceeds
`firewall_threshold` (default: 2000 chars):

1. The raw output is stored in `ArtifactStore` (out-of-band)
2. `RuleBasedSummarizer` generates a concise summary (head + tail + truncation)
3. `StructuredExtractor` pulls out structured facts (keys, entities, numbers)
4. A `ContextItem` with the summary text and an `artifact_ref` goes into the log
5. A `ResultEnvelope` wrapping summary, facts, artifacts, and views is returned

The net effect: instead of 10,000 tokens of raw output, the LLM sees ~75
tokens of summary plus a handle for drilldown.

### Structured Extraction

The `StructuredExtractor` detects the content type and extracts accordingly:

- **JSON object**: top-level keys, value types, array lengths, bounded sample
- **JSON array**: row count, column names, first 3 rows
- **Plain text**: line count, section headings, entities (emails, URLs, numbers)

The extracted facts go into `ResultEnvelope.facts` and can be used by the
agent framework for routing decisions without consuming prompt tokens.

## Bounded Choice

### The Problem: Tool-Space Interference

When an agent has access to many tools (50+), including all tool descriptions
in the prompt creates interference: the LLM wastes attention on irrelevant
options, sometimes hallucinating tool calls to tools that seem tangentially
related.

### The Solution: Routing Engine

contextweaver's Routing Engine provides bounded-choice navigation:

1. **Catalog** -- all tools registered as `SelectableItem` objects
2. **TreeBuilder** -- partitions tools into a bounded tree (`ChoiceGraph`)
3. **Router** -- beam-search traversal to find the top-k relevant tools
4. **Choice Cards** -- compact, LLM-friendly rendering (max 20 cards)

Instead of showing the LLM 100 tool descriptions, contextweaver shows 5-10
relevant choices in a compact card format. The LLM picks from a focused menu
rather than an overwhelming catalog.

### Tree Construction

The `TreeBuilder` uses three partitioning strategies in order:

1. **Namespace grouping** -- if tools have namespaces (e.g. `billing.*`, `crm.*`)
2. **Jaccard clustering** -- farthest-first seeding on description tokens
3. **Alphabetical bucketing** -- sorted by name, split into chunks

Each level of the tree has at most `max_children` branches (default: 20).
This guarantees bounded branching factor at every level.

## Choice Cards

A `ChoiceCard` is the compact representation shown to the LLM:

```
[1/5] billing.invoices.search (tool) -- Search invoices [billing, search] score=0.82
```

Key properties:
- **No schemas** -- full `args_schema` is never included (available via drilldown)
- **Bounded count** -- max 20 cards per presentation
- **Bounded description** -- max 240 chars, truncated with "..."
- **Scores included** -- so the LLM can see relative relevance
- **Deterministic order** -- sorted by score descending, ties by ID

### Why No Schemas in Cards?

Including JSON schemas in the tool selection prompt is wasteful:
- Schemas can be 500+ tokens each
- The LLM does not need schemas to *choose* a tool, only to *call* it
- Schemas are provided in the CALL phase after selection

This separation between "choose" (ROUTE) and "call" (CALL) is a key
contextweaver design principle.

# Architecture

This document covers the internal architecture of contextweaver: its data
model, store layers, execution phases, candidate pipeline stages, result
envelope design, routing DAG, and key design decisions.

## System Overview

contextweaver consists of two integrated engines:

```
                   +------------------------------------------+
                   |            contextweaver                  |
                   |                                          |
                   |   +------------------+  +-------------+  |
  User query  -->  |   | Context Engine   |  | Routing     |  |
  Tool results -->  |   | (CEA pipeline)   |  | Engine      |  |
  Agent messages -> |   |                  |  | (DAG + beam |  |
                   |   | Phase-specific   |  |  search)    |  |
                   |   | budget-bounded   |  |             |  |
                   |   | context compiler |  | Bounded-    |  |
                   |   |                  |  | choice nav  |  |
                   |   +------------------+  +-------------+  |
                   |           |                    |          |
                   |   +------+--------------------+-------+  |
                   |   |          Store Layer               |  |
                   |   | EventLog | ArtifactStore | Facts  |  |
                   |   |          | EpisodicStore          |  |
                   |   +-----------------------------------+  |
                   +------------------------------------------+
```

## Data Model

### Core Types

```
ContextItem
  - id: str                   Unique identifier
  - kind: ItemKind            Semantic type (USER_TURN, TOOL_RESULT, ...)
  - text: str                 Content visible to the LLM
  - token_estimate: int       Approximate token count
  - metadata: dict            Arbitrary key-value pairs
  - parent_id: str | None     Links TOOL_RESULT -> TOOL_CALL
  - artifact_ref: str | None  Handle into ArtifactStore

SelectableItem
  - id: str                   Unique tool/agent identifier
  - kind: "tool"|"agent"|"skill"|"internal"
  - name: str                 Human-readable name
  - description: str          What this item does
  - tags: list[str]           For scoring and grouping
  - namespace: str            Hierarchical grouping key
  - args_schema: dict | None  JSON Schema (never sent to LLM in cards)
  - side_effects: bool        Whether invocation mutates state
  - cost_hint: "free"|"low"|"medium"|"high"

ResultEnvelope
  - status: "ok"|"partial"|"error"
  - summary: str              LLM-friendly summary of the raw output
  - facts: dict               Structured extraction
  - artifacts: list[ArtifactRef]  References to out-of-band storage
  - views: list[ViewSpec]     Named projections (head, json_keys, rows)
  - provenance: dict          Source tracking metadata
```

### Enumerations

```
ItemKind:  USER_TURN | AGENT_MSG | TOOL_CALL | TOOL_RESULT
           | DOC_SNIPPET | MEMORY_FACT | PLAN_STATE | POLICY

Phase:     ROUTE | CALL | INTERPRET | ANSWER

Sensitivity: PUBLIC | INTERNAL | CONFIDENTIAL | RESTRICTED
```

## Store Layers

contextweaver uses four append-only / last-write-wins stores that together
form the persistent state of an agent session.

```
+-------------------+--------------------------------------------+
| Store             | Purpose                                    |
+-------------------+--------------------------------------------+
| InMemoryEventLog  | Append-only log of all ContextItems.       |
|                   | The ground truth for the context pipeline.  |
+-------------------+--------------------------------------------+
| InMemoryArtifact  | Out-of-band storage for large raw outputs. |
| Store             | Keyed by handle; supports drilldown.        |
+-------------------+--------------------------------------------+
| InMemoryFactStore | Durable semantic facts. Key-value,          |
|                   | last-write-wins. Survives across phases.    |
+-------------------+--------------------------------------------+
| InMemoryEpisodic  | Rolling summaries of conversation           |
| Store             | segments. Ordered by insertion time.         |
+-------------------+--------------------------------------------+
```

All four are bundled in `StoreBundle` for clean initialization:

```python
from contextweaver.store import StoreBundle
bundle = StoreBundle(event_log=my_log, artifact_store=my_store)
mgr = ContextManager(stores=bundle)
```

If any store is `None`, `ContextManager` creates a default in-memory instance.

## Execution Phases

Every LLM invocation in an agent loop serves a specific purpose. contextweaver
models this as four phases, each with its own token budget and item-kind filter:

```
  Phase       Budget   Allowed Kinds               Purpose
  ----------  ------   -------------------------   -------------------------
  ROUTE       2000     USER_TURN, PLAN_STATE,      Choose which tool/agent
                       POLICY                      to invoke

  CALL        3000     USER_TURN, AGENT_MSG,       Prepare the tool
                       TOOL_CALL, PLAN_STATE,      invocation arguments
                       POLICY

  INTERPRET   4000     All except restricted        Process the tool result
                       (adds TOOL_RESULT,           and extract meaning
                        DOC_SNIPPET, MEMORY_FACT)

  ANSWER      6000     All ItemKinds               Compile the final
                                                   user-facing response
```

The phase determines:
1. Which ItemKinds are eligible (ContextPolicy.allowed_kinds_per_phase)
2. How many tokens are available (ContextBudget.for_phase)
3. How items are scored (kind_priority weights differ per phase)

## Context Engine Pipeline

The Context Engine Assembly (CEA) pipeline has five stages:

```
  Event Log
      |
      v
  +-------------------+
  | 1. CANDIDATE      |  Filter by phase, sensitivity, TTL, redaction hooks
  |    GENERATION     |  -> list[ContextItem]
  +-------------------+
      |
      v
  +-------------------+
  | 2. SCORING        |  Score each candidate on four axes:
  |                   |    - recency_weight      (0.30)
  |                   |    - tag_match_weight    (0.25)
  |                   |    - kind_priority_weight (0.35)
  |                   |    - token_cost_penalty  (0.10)
  |                   |  -> list[(ContextItem, float)] sorted desc
  +-------------------+
      |
      v
  +-------------------+
  | 3. DEDUPLICATION  |  MD5 hash of item.text; keep higher-scored copy
  |                   |  -> deduplicated list, removed count
  +-------------------+
      |
      v
  +-------------------+
  | 4. SELECTION      |  Greedy budget packing with dependency closure:
  |    & PACKING      |    - TOOL_RESULT pulls in its parent TOOL_CALL
  |                   |    - Items packed until budget exhausted
  |                   |  -> included, excluded (with reasons), closures
  +-------------------+
      |
      v
  +-------------------+
  | 5. RENDERING      |  Deterministic section-based rendering:
  |                   |    - Known Facts section
  |                   |    - Recent Context (episodic) section
  |                   |    - Context Items section (with labels)
  |                   |  -> rendered_text, tokens_per_section
  +-------------------+
      |
      v
  ContextPack
    .rendered_text         Final prompt text for the LLM
    .included_items        Items that made it into context
    .excluded_items        Items dropped (with reasons: budget/dedup/score)
    .budget_used           Tokens consumed
    .budget_total          Tokens available
    .artifacts_available   Handles for drilldown
    .facts_snapshot        Current fact store contents
    .episodic_summaries    Recent episode summaries
    .stats                 BuildStats with full pipeline telemetry
```

### Context Firewall

The firewall sits between raw tool output and the event log. When
`len(raw_output) > firewall_threshold`:

```
  Raw output (e.g. 10 000 chars)
      |
      +---> ArtifactStore.put(handle, raw_bytes)     [out-of-band storage]
      |
      +---> RuleBasedSummarizer.summarize(text)       [head+tail summary]
      |
      +---> StructuredExtractor.extract(text)          [entities, keys, etc.]
      |
      v
  ContextItem(text=summary, artifact_ref=handle)      [~300 chars in context]
  ResultEnvelope(summary, facts, artifacts, views)     [structured metadata]
```

If the output is small (under threshold), the full text goes into the
ContextItem and no artifact is stored.

### Dependency Closure

During selection (stage 4), when a TOOL_RESULT is included, the packer
checks whether its parent TOOL_CALL is already included. If not, the parent
is pulled in automatically (counted as a "dependency closure"). Both items
must fit within the remaining budget.

## Result Envelope

The `ResultEnvelope` is the structured wrapper for every tool output:

```
ResultEnvelope
  status: "ok" | "partial" | "error"
  summary: str              LLM sees this (concise, human-readable)
  facts: dict               Structured extraction (keys, types, entities)
  artifacts: [ArtifactRef]  Handles to full raw data
  views: [ViewSpec]         Named drilldown projections
  provenance: dict          Source tracking
```

The separation between `summary` (in context) and `artifacts` (out-of-band)
is the core mechanism that prevents output swamping.

## Routing Engine

### Catalog and Graph

```
  Flat catalog (list[SelectableItem])
      |
      v
  TreeBuilder.build(items)
      |
      |  1. Partition by namespace (if >= 2 groups)
      |  2. Fallback: Jaccard-seeded clustering
      |  3. Fallback: alphabetical bucketing
      |  4. Recurse until group_size <= max_children
      |
      v
  ChoiceGraph (bounded tree)
      .root_id       -> "root"
      .nodes         -> {node_id: ChoiceNode}
      .items         -> {item_id: SelectableItem}
      .max_children  -> 20 (configurable)
```

### ChoiceNode Structure

```
ChoiceNode
  - node_id: str            Hierarchical ID (e.g. "root", "g0", "g0.1")
  - label: str              Auto-generated label from KeywordLabeler
  - routing_hint: str       "Tools related to {label}"
  - children: list[str]     Child node or item IDs
  - child_types: dict       Maps child_id -> "node" | "item"
```

### Beam-Search Router

```
  Query: "search the database for customer records"
      |
      v
  Router.route(query)
      |
      |  beam = [(0.0, [root], root)]
      |
      |  For each depth level (max_depth=8):
      |    For each beam entry:
      |      Score all children against query
      |        - Nodes: Jaccard(query_tokens, label_tokens)
      |        - Items: TF-IDF score from pre-fitted corpus
      |      Expand top beam_width children
      |      If score gap < confidence_gap: expand one extra
      |    Keep top beam_width beams
      |    Collect leaf items with scores
      |
      v
  RouteResult
    .candidate_items   Top-k SelectableItems
    .candidate_ids     Their IDs
    .paths             Navigation paths through the tree
    .scores            {item_id: float}
```

### Choice Cards

Choice cards are the compact, LLM-friendly representation:

```
  [1/5] billing.invoices.search (tool) — Search invoices [billing, search] score=0.82
  [2/5] billing.payments.list (tool) — List payments [billing, payment] score=0.71
```

Enforced limits:
- Max 20 cards (configurable)
- Max 240 chars per description (truncated with "...")
- Optional total char limit (drops lowest-scored)
- No schemas in cards (schemas available via drilldown)

## Design Decisions

### Async-First with Sync Wrappers

All store operations and the build pipeline are `async def`. Sync wrappers
(e.g. `ingest_sync`, `build_sync`) use `asyncio.run()` or thread-pool
delegation when an event loop is already running. This allows:
- Native async integration in modern frameworks
- Zero-friction use in synchronous scripts
- Future support for async store backends (Redis, databases)

### Zero Runtime Dependencies

contextweaver has zero runtime dependencies. All utilities (tokenization,
Jaccard similarity, TF-IDF scoring, JSON parsing, MD5 hashing) are
implemented in pure Python. This means:
- No version conflicts with host applications
- Predictable behavior across environments
- Easy vendoring into monorepos

### Deterministic Output

All rendering, serialization, and scoring operations are deterministic:
- Dict keys are always sorted in serialization
- Items are sorted by ID for tie-breaking
- No random state in the default pipeline
- TF-IDF corpus is fitted once and reused

### Append-Only Event Log

The event log is append-only by design. Items are never mutated after
insertion. This ensures:
- Reproducible context builds from the same log state
- Safe concurrent reads during pipeline stages
- Simple debugging (the log is the full history)

### Protocol-Based Extension Points

All extension points use `typing.Protocol`:
- `TokenEstimator` — custom token counting
- `Summarizer` — custom summarization strategies
- `Extractor` — custom structured extraction
- `RedactionHook` — custom PII/sensitivity redaction
- `Labeler` — custom group labeling for the routing tree
- `EventHook` — lifecycle callbacks for observability

## Module Map

```
src/contextweaver/
  types.py          Dataclasses and enums (no logic)
  config.py         Configuration dataclasses
  protocols.py      Protocol interfaces and trivial defaults
  exceptions.py     Exception hierarchy
  _utils.py         Text similarity (tokenize, jaccard, TfIdfScorer)
  serde.py          Serialization helpers

  store/
    __init__.py     StoreBundle re-exports
    event_log.py    InMemoryEventLog (append-only)
    artifacts.py    InMemoryArtifactStore (with drilldown)
    facts.py        InMemoryFactStore (key-value)
    episodic.py     InMemoryEpisodicStore (rolling summaries)

  context/
    __init__.py     Re-exports
    manager.py      ContextManager + ContextPack
    candidates.py   Stage 1: candidate generation
    scoring.py      Stage 2: multi-axis scoring
    dedup.py        Stage 3: content-hash deduplication
    selection.py    Stage 4: greedy budget packing
    prompt.py       Stage 5: deterministic rendering + PromptBuilder
    firewall.py     Context firewall (intercept + summarize + store)

  routing/
    __init__.py     Re-exports
    catalog.py      Catalog loading and generation
    graph.py        ChoiceGraph + ChoiceNode
    tree.py         TreeBuilder (recursive bounded partitioning)
    router.py       Router (beam search) + RouteResult
    cards.py        ChoiceCard rendering
    labeler.py      KeywordLabeler (auto-labeling)

  summarize/
    __init__.py     Re-exports
    rules.py        RuleBasedSummarizer (head+tail, JSON overview)
    extract.py      StructuredExtractor (entities, keys, types)

  adapters/
    __init__.py     Re-exports
    mcp.py          MCP protocol conversion + JSONL loading
    a2a.py          A2A protocol conversion + JSONL loading

  __main__.py       CLI entry point
```

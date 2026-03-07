# Concepts

This document explains the core concepts in contextweaver.

## Context Item

A `ContextItem` is the atomic unit of the event log. Every user turn,
agent message, tool call, tool result, documentation snippet, memory
fact, plan state, or policy rule is represented as a `ContextItem`.

Key fields:

| Field | Description |
|---|---|
| `id` | Unique identifier |
| `kind` | One of the `ItemKind` enum values |
| `text` | The textual content |
| `parent_id` | Optional link to a parent item (e.g. tool_result → tool_call) |
| `token_estimate` | Pre-computed token count (optional) |
| `sensitivity` | Data sensitivity level (`public`, `internal`, `confidential`, `restricted`) |
| `metadata` | Arbitrary key-value metadata |

## Phases

contextweaver organises agent execution into four phases, each with its
own token budget:

- **route** — selecting which tool(s) to call.
- **call** — preparing tool call arguments.
- **interpret** — understanding tool results.
- **answer** — composing the final response to the user.

The `ContextBudget` dataclass defines the token limit for each phase.
Different phases emphasise different item kinds — for example, the
`answer` phase prioritises user turns and tool results, while the
`route` phase prioritises tool descriptions.

## Selectable Item (ToolCard)

A `SelectableItem` is the unified representation of anything the Routing
Engine can select — a tool, agent, skill, or internal function. The type
alias `ToolCard` is used when emphasising the LLM-facing card framing.

Key fields: `id`, `kind`, `name`, `description`, `tags`, `namespace`,
`side_effects`, `cost_hint`.

## Context Firewall

The context firewall prevents large tool outputs from consuming the
entire token budget. When a tool result exceeds the configured threshold
(default 2 000 characters), the firewall:

1. Stores the raw output in the `ArtifactStore`.
2. Generates a compact summary using the `Summarizer`.
3. Extracts structured facts for the `FactStore`.
4. Replaces the original item text with a summary + artifact reference.

## Result Envelope

A `ResultEnvelope` captures the processed output of a tool call:

- `summary` — compact text summary of the result.
- `facts` — list of extracted factual statements.
- `artifacts` — list of `ArtifactRef` handles for raw data.
- `views` — optional alternative representations.
- `status` — success / error / partial.

## Sensitivity Enforcement

Each `ContextItem` has a `sensitivity` field (default: `public`) that
classifies its data sensitivity level. The `ContextPolicy.sensitivity_floor`
setting (default: `confidential`) determines which items are subject to
filtering during context compilation.

Items whose sensitivity level meets or exceeds the floor are either:

- **Dropped** (`sensitivity_action="drop"`, the default) — removed from
  the candidate list before scoring or rendering.
- **Redacted** (`sensitivity_action="redact"`) — text replaced with
  `[REDACTED: {sensitivity}]` via the `MaskRedactionHook`, while
  preserving all item metadata.

Dropped items are recorded in `BuildStats.dropped_reasons["sensitivity"]`.

## Build Stats

Every context build produces a `BuildStats` object that explains exactly
what happened:

- How many candidates were generated.
- How many were included, dropped, or deduplicated.
- Token usage per section.
- Which items were dropped and why.
- Dependency closures applied.

## Choice Graph

The `ChoiceGraph` is a bounded DAG used by the Routing Engine. Interior
nodes are labelled navigation points; leaf nodes are items from the
catalog. The `TreeBuilder` constructs the graph using one of three
strategies:

1. **Namespace grouping** — items sharing a namespace prefix are grouped.
2. **Jaccard clustering** — farthest-first seeding + nearest assignment
   based on text similarity.
3. **Alphabetical fallback** — sorted by name, split into labelled
   chunks.

The `Router` performs beam search over this graph, scoring each path
to find the top-k most relevant items for a given query.

## Choice Cards

A `ChoiceCard` is the LLM-friendly representation of a routing result.
It contains the item name, description, relevance score, and optional
side-effect warning — but **never** the full argument schema. This keeps
the LLM's context focused on *which* tool to use, not *how* to call it.

## Episodic Memory & Facts

contextweaver supports two forms of persistent memory:

- **EpisodicStore** — stores short summaries of past interactions,
  keyed by episode ID. These are injected into the prompt header.
- **FactStore** — stores key-value pairs (e.g. `user_timezone=UTC`).
  Facts are injected into the prompt alongside episodic memory.

Both are capped in the prompt to prevent memory from crowding out the
current conversation.

## View Registry

The `ViewRegistry` (in `context/views.py`) maps content-type patterns to view
generators. When the context firewall stores a large tool output as an artifact,
the view system can generate alternative representations — a JSON subset, a CSV
summary, or a column listing — that the agent can request via drilldown without
retrieving the full blob. This progressive-disclosure mechanism keeps the
context window focused while preserving access to the raw data.

## Hydration Result

A `HydrationResult` (in `envelope.py`) captures the output of hydrating a
tool call with context. Hydration enriches a tool call's arguments or
description with context-aware information before execution, and the
`HydrationResult` carries both the enriched payload and metadata about
what context was used.

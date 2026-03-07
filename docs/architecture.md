# Architecture

contextweaver is structured around two cooperating engines that together solve
the "context window problem" for tool-using AI agents.

## High-level overview

```
               ┌────────────────────────────┐
  Events ─────>│      Context Engine         │──> ContextPack (prompt)
               │  candidates → score →       │
               │  dedup → select → firewall  │
               │  → prompt                   │
               └────────────────────────────┘
                          ▲ facts / episodes
               ┌──────────┴─────────────────┐
  Tools ──────>│      Routing Engine         │──> ChoiceCards
               │  Catalog → TreeBuilder →    │
               │  ChoiceGraph → Router       │
               └────────────────────────────┘
```

## Package layout

| Path | Responsibility |
|---|---|
| `types.py` | Core dataclasses and enums (`SelectableItem`, `ContextItem`, `Phase`, `ItemKind`) |
| `envelope.py` | Result types (`ResultEnvelope`, `BuildStats`, `ContextPack`, `ChoiceCard`, `HydrationResult`) |
| `config.py` | Configuration dataclasses (`ContextBudget`, `ContextPolicy`, `ScoringConfig`) |
| `protocols.py` | Protocol interfaces (`TokenEstimator`, `EventHook`, `Summarizer`, …) |
| `exceptions.py` | Custom exception hierarchy |
| `_utils.py` | Text similarity primitives (`tokenize`, `jaccard`, `TfIdfScorer`) |
| `serde.py` | Serialisation helpers for `to_dict` / `from_dict` patterns |
| `store/` | In-memory data stores (`EventLog`, `ArtifactStore`, `EpisodicStore`, `FactStore`) |
| `summarize/` | Rule engine and structured fact extraction |
| `context/` | Full context compilation pipeline |
| `routing/` | Catalog, DAG builder, beam-search router, card renderer |
| `adapters/` | MCP and A2A protocol adapters |
| `__main__.py` | CLI entry point (7 subcommands) |

## Context Engine pipeline

The Context Engine compiles a phase-aware, budget-constrained prompt from
the event log. The pipeline has eight stages:

1. **generate_candidates** — pull events from the event log and inject
   episodic memory and facts into the candidate pool.
2. **dependency_closure** — if a selected item has a `parent_id`, bring
   the parent along even if it scored lower.
3. **sensitivity_filter** — drop or redact items whose `sensitivity`
   level meets or exceeds `ContextPolicy.sensitivity_floor`.
4. **apply_firewall** — large tool results (above threshold) are
   summarised; the raw output is stored in the ArtifactStore and replaced
   with a compact reference + summary.
5. **score_candidates** — rank candidates by recency, tag match, kind
   priority, and token cost.
6. **deduplicate_candidates** — remove near-duplicate items using Jaccard
   similarity over tokenised text.
7. **select_and_pack** — greedily pack the highest-scoring candidates
   into the token budget for the current phase.
8. **render_context** — assemble the final prompt string, grouped by
   section (facts, history, tool results), with `BuildStats` metadata.

## Routing Engine pipeline

The Routing Engine efficiently navigates large tool catalogs so the LLM
never sees all tools at once:

1. **Catalog** — register and manage `SelectableItem` objects.
2. **TreeBuilder** — convert a flat item list into a bounded
   `ChoiceGraph` DAG using namespace grouping, Jaccard clustering, or
   alphabetical fallback.
3. **Router** — beam-search over the graph to find the top-k items most
   relevant to a user query.
4. **ChoiceCards** — render compact, LLM-friendly cards for the selected
   items (never includes full schemas).

## Data stores

All stores are protocol-based with in-memory defaults:

- **EventLog** — append-only log of `ContextItem` events.
- **ArtifactStore** — blob storage for raw tool outputs intercepted by
  the firewall.
- **EpisodicStore** — short episodic memory entries (keyed by episode ID).
- **FactStore** — key-value fact entries persisted across turns.
- **StoreBundle** — convenience wrapper grouping all four stores.

## Progressive disclosure

`context/views.py` provides a `ViewRegistry` that maps content-type patterns
to view generators. When the firewall stores a large tool output as an artifact,
the view system generates alternative representations (JSON subset, CSV summary,
etc.) the agent can drilldown into without retrieving the full blob.
`drilldown_tool_spec()` exposes drilldown as an agent-callable tool.

## Design principles

- **Zero runtime dependencies** — stdlib-only, Python ≥ 3.10.
- **Deterministic** — tie-break by ID, sorted keys.
- **Protocol-based** — all store and estimator interfaces are
  `typing.Protocol`, allowing custom implementations.
- **Async-first** — the Context Engine exposes `build()` (async) with a
  `build_sync()` wrapper for synchronous callers.
- **Budget-aware** — every build is constrained by the phase-specific
  token budget; `BuildStats` explains what was kept and what was dropped.

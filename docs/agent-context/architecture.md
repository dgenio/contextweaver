# Architecture Guidance

> Deeper architectural detail lives in [docs/architecture.md](../architecture.md).
> This file covers non-obvious design decisions relevant to change-scoping.

## Architectural Intent

contextweaver separates **what to show the LLM** (Context Engine) from **which tools to offer** (Routing Engine). These engines share types and stores but have intentionally different execution models:

- **Context Engine** — async-first. Deals with I/O-bound operations (event log queries, artifact storage).
- **Routing Engine** — sync-only. Pure computation (DAG traversal, beam search). No I/O.

This boundary is intentional. Do not propose making routing async "for consistency" — it adds complexity for zero benefit.

## Non-Goals

contextweaver is **not** an LLM inference layer and **not** a tool execution runtime. It prepares context and routes tools but never calls models or executes tools. Feature proposals that cross these boundaries are out of scope.

## Major Boundaries

### Context Pipeline (8 stages)

The pipeline is a fixed sequence. Each stage has a single responsibility:

1. **generate_candidates** — pulls events from stores into a candidate pool
2. **dependency_closure** — ensures parent items (via `parent_id`) are included alongside their children
3. **sensitivity_filter** — drops or redacts items above the sensitivity floor
4. **apply_firewall** — summarises large outputs, stores raw data as artifacts
5. **score_candidates** — ranks candidates by recency, tag match, kind priority, token cost
6. **deduplicate_candidates** — removes near-duplicates via Jaccard similarity
7. **select_and_pack** — greedily packs highest-scoring candidates into the phase token budget
8. **render_context** — assembles the final prompt with BuildStats metadata

**Why this order matters:**
- Dependency closure must happen before scoring, otherwise parents could be discarded before their children pull them in.
- Sensitivity filtering before the firewall prevents sensitive data from reaching the summarizer.
- Scoring after the firewall ensures scores reflect the summarised (not raw) content.

### Routing Pipeline (4 stages)

1. **Catalog** — register and manage `SelectableItem` objects
2. **TreeBuilder** — convert flat items into a bounded `ChoiceGraph` DAG
3. **Router** — beam-search over the graph for top-k relevant items
4. **ChoiceCards** — render compact LLM-friendly cards (never full schemas)

### Store Layer

All stores use `typing.Protocol` interfaces with in-memory defaults. This enables custom backends (database, Redis, etc.) without changing pipeline code.

- **EventLog** — append-only. The audit trail.
- **ArtifactStore** — raw tool outputs stored by the firewall. Supports drilldown via `ViewRegistry`.
- **EpisodicStore** — short episodic memory entries.
- **FactStore** — key-value facts persisted across turns.

Any backend can prove it honours these protocols with the shipped conformance
kit (`contextweaver.store.testing`, issue #520): each `check_*_conformance`
function takes a factory for an empty backend and asserts the round-trip,
ordering, and not-found semantics the Context Engine relies on. For the
`ArtifactStore` it also asserts that `put()` stamps a sha256 `content_hash`
on the returned ref — the firewall's content-addressed idempotency
short-circuit (#190) depends on it, so it is a protocol contract, not a
backend detail. The bundled in-memory, JSON-file, and SQLite backends are all
run through it in `tests/test_store_conformance.py`.

#### Thread-safety contract (issue #458)

The store protocols make **no concurrency guarantee** in their interface; each
backend documents its own. The bundled backends:

- **`InMemory*` stores** are *not* thread-safe. They are for single-threaded
  use and tests; guard them with your own lock for concurrent access.
- **`JsonFileArtifactStore`** is single-process. Within one process it is
  thread-safe: `put` / `delete` / `list_refs` on a shared instance are
  serialised by an internal lock, and each individual file write is **atomic**
  (temp file + `os.replace`), so a reader never observes a torn or truncated
  artifact and a crash mid-write leaves the previous version intact. There is
  no cross-process advisory locking, so two processes writing the same
  `base_dir` are still unsupported.
- **`SqliteEventLog`** opens its connection in WAL mode for single-process use;
  it is not shared across threads.

The gateway runtime (`ProxyRuntime`) inherits these guarantees through the
store it is given: its read-only `tool_view` (drilldown) is safe to call
concurrently against a `JsonFileArtifactStore`. A gateway that fans out to
real concurrent clients should pick (or wrap) a backend whose contract matches
its load — this is exactly what the protocol seam and conformance kit are for.

### Sensitivity Enforcement

`context/sensitivity.py` is security-grade code. It enforces data classification (`public` → `restricted`) with two actions: drop or redact. The `MaskRedactionHook` is the built-in redactor. Changes to this module require extra review scrutiny — never weaken defaults.

### Progressive Disclosure (ViewRegistry)

`context/views.py` provides a `ViewRegistry` that maps content-type patterns to view generators. When the firewall stores a large tool output as an artifact, the view system generates alternative representations (JSON subset, CSV summary, etc.) the agent can drilldown into without retrieving the full blob. `drilldown_tool_spec()` exposes drilldown as an agent-callable tool.

## Key Tradeoffs

| Decision | Tradeoff | Consequence of reversing |
|---|---|---|
| Protocol-based stores | More files and indirection | Allows backend swaps without pipeline changes |
| `to_dict()`/`from_dict()` + `serde.py` | Two serialization paths | Per-class methods handle class-specific logic; `serde.py` handles shared primitives. Consolidating loses encapsulation. |
| Sync routing / async context | Two calling conventions | Routing has no I/O — async would add overhead for zero benefit |
| 8-stage pipeline | Pipeline is long | Each stage has a single well-defined responsibility. Merging stages creates coupling. |
| ChoiceCards never include schemas | Limits LLM tool-call generation | Keeps routing focused on *which* tool, not *how* — schema is provided at call-time via hydration |

## Structural Mental Model

Think of contextweaver as three layers:

1. **Data layer** (`types.py`, `envelope.py`, `config.py`, `serde.py`, `exceptions.py`) — pure data, no I/O, no side effects.
2. **Store layer** (`store/`, `protocols.py`) — stateful but simple append-only/read interfaces.
3. **Pipeline layer** (`context/`, `routing/`, `summarize/`) — orchestration logic that reads from stores and produces output types.

Adapters (`adapters/`) convert external formats (MCP, FastMCP, A2A) into contextweaver types at the boundary.

Changes should flow within a layer. Cross-layer changes (e.g., adding I/O to the data layer) are red flags.

# contextweaver — Agent Guide

> **Authority:** This file is the single source of truth for agent-facing guidance.
> Tool-specific files (`.claude/CLAUDE.md`, `.github/copilot-instructions.md`) contain
> only overrides and guardrails — they defer to this file for all shared rules.

## Purpose

contextweaver is a Python library for dynamic context management for tool-using AI agents.
It provides two integrated engines:

1. **Context Engine** — phase-specific budgeted context compilation with a context firewall
2. **Routing Engine** — bounded-choice navigation over large tool catalogs via DAG + beam search

**Non-goals:** contextweaver is not an LLM inference layer and not a tool execution runtime.
It prepares context and routes tools but never calls models or executes tools.

## Module Map

| Path | Responsibility |
|---|---|
| `types.py` | Core dataclasses and enums: `SelectableItem`, `ContextItem`, `Phase`, `ItemKind`, `Sensitivity` |
| `envelope.py` | Result types: `ResultEnvelope`, `BuildStats`, `ContextPack`, `ChoiceCard`, `HydrationResult`, `RoutingDecision` |
| `config.py` | Configuration: `ContextBudget`, `ContextPolicy`, `ScoringConfig` |
| `profiles.py` | Routing and profile config: `Mode`, `RoutingConfig`, `ProfileConfig`, named presets |
| `protocols.py` | Protocol interfaces: `TokenEstimator`, `EventHook`, `Summarizer`, `Extractor`, `RedactionHook`, `Labeler`, `Retriever`, `Reranker`, `ClusteringEngine` (store protocols re-exported from `store/protocols.py`) |
| `store/protocols.py` | Store-layer protocols: `EventLog`, `ArtifactStore`, `EpisodicStore`, `FactStore` |
| `exceptions.py` | Custom exception hierarchy (all errors inherit `ContextWeaverError`) |
| `_utils.py` | Text similarity primitives: `tokenize()`, `jaccard()`, `TfIdfScorer` |
| `serde.py` | Serialisation helpers for `to_dict` / `from_dict` |
| `store/` | In-memory data stores: `EventLog`, `ArtifactStore`, `EpisodicStore`, `FactStore`, `StoreBundle` |
| `summarize/` | `SummarizationRule`, `RuleEngine`, `extract_facts()` |
| `context/` | Full context pipeline, sensitivity enforcement, view registry, `ContextManager` |
| `context/ingest.py` | Tool-result ingestion helpers (extracted from `manager.py` to honor the <=300 line guideline) |
| `routing/` | `Catalog`, `ChoiceGraph`, `TreeBuilder`, `Router` (beam search), card renderer |
| `routing/filters.py` | Pre-scoring helpers: `filter_items()`, `augment_query()`, `suggest_clarifying_question()` (issues #14, #22, #112, #116) |
| `routing/manifest.py` | `GraphManifest` + `compute_catalog_hash()` for graph metadata and cache invalidation (issue #48, #15) |
| `routing/normalizer.py` | `CatalogNormalizer` + `NormalizationReport` for catalog metadata hygiene (issue #44) |
| `routing/registry.py` | `EngineRegistry` and bundled `TfIdfRetriever` / `NoOpReranker` / `JaccardClusteringEngine` defaults (issue #47) |
| `routing/trace.py` | `RouteTrace` + `TraceStep` structured routing audit (issue #51) |
| `adapters/` | MCP, FastMCP, A2A, and weaver-spec protocol adapters |
| `__main__.py` | CLI: 7 subcommands (`demo`, `build`, `route`, `print-tree`, `init`, `ingest`, `replay`) |

## Pipelines (summary)

**Context Engine** — 8 stages:

1. `generate_candidates` → 2. `dependency_closure` → 3. `sensitivity_filter` →
4. `apply_firewall` → 5. `score_candidates` → 6. `deduplicate_candidates` →
7. `select_and_pack` → 8. `render_context`

**Routing Engine** — 4 stages:

1. `Catalog` → 2. `TreeBuilder` → 3. `Router` (beam search) → 4. `ChoiceCards`

For full pipeline descriptions and design rationale, see [docs/agent-context/architecture.md](docs/agent-context/architecture.md).

## Key Types

| Type | Purpose |
|---|---|
| `SelectableItem` | Unified tool/agent/skill/internal item. Alias: `ToolCard` (use `SelectableItem` in code). |
| `ContextItem` | Event log entry with `parent_id` for dependency closure |
| `ResultEnvelope` | Processed tool output: summary + facts + artifacts + views |
| `ContextPack` | Rendered prompt + stats from a context build |
| `BuildStats` | What was kept, dropped, and why — diagnostic output of every build |
| `ChoiceCard` | LLM-friendly compact card (never includes full schemas) |
| `RoutingDecision` | Spec-aligned routing output (id, choice_cards, timestamp, selection); build via `RouteResult.to_routing_decision(...)` |
| `ChoiceGraph` | Bounded DAG for routing, serializable, validated on load |
| `GraphManifest` | Build-time metadata attached to every routing graph (hash, seed, engine versions, timestamp) |
| `RouteTrace` | Always-populated structured audit of a routing call; per-step expansions opt-in via `debug=True` |
| `EngineRegistry` | Pluggable registry for `Retriever`, `Reranker`, `ClusteringEngine` slots |
| `Mode` | Determinism mode (`strict` / `seeded` / `adaptive` placeholder) on `ProfileConfig` |
| `MaskRedactionHook` | Built-in redaction hook for sensitivity enforcement |
| `HydrationResult` | Result of hydrating a tool call with context |
| `ViewRegistry` | Maps content-type patterns to view generators for progressive disclosure |

**Vocabulary notes:**
- `SelectableItem` is the canonical name. `ToolCard` is a user-facing alias — use `SelectableItem` in code and docs.
- "Context" is overloaded — can mean `ContextItem`, `ContextPack`, the pipeline, or the LLM context window. Disambiguate when unclear. See [docs/concepts.md](docs/concepts.md).
- "Firewall" here means context firewall (prevents large outputs from consuming the token budget), not a security firewall.

## Commands

```bash
make fmt      # ruff format src/ tests/ examples/
make lint     # ruff check src/ tests/ examples/
make type     # mypy src/
make test     # pytest --cov=contextweaver --cov-report=term-missing -q
make example  # run all example scripts
make demo     # python -m contextweaver demo
make ci       # fmt + lint + type + test + example + demo
make docs     # mkdocs build --clean (docs site)
make docs-serve  # mkdocs serve (live preview)
make benchmark   # run benchmark harness (non-gating; writes benchmarks/results/latest.json)
make llms        # regenerate llms.txt and llms-full.txt from canonical docs
make llms-check  # verify llms.txt and llms-full.txt are up to date (exits non-zero on drift)
```

Run `pre-commit install` once after cloning to activate git hooks
(ruff format + check + file hygiene on every commit).

For command-selection rules and sequencing, see [docs/agent-context/workflows.md](docs/agent-context/workflows.md).

## Hard Rules

These are auto-reject in review. No exceptions.

1. **No `print()` in library code.** Use hooks or logging. `__main__.py` (CLI) is exempt.
2. **No business logic in `__init__.py`.** Only re-exports allowed.

## Strong Patterns

These are strongly recommended. Engineering judgment applies — deviate with good reason.

- **Text similarity in `_utils.py` only** — `tokenize()`, `jaccard()`, `TfIdfScorer` are the single source of truth. Do not duplicate.
- **`from __future__ import annotations`** in every source file.
- **All exceptions from `contextweaver.exceptions`** — use the custom hierarchy, not bare `ValueError`/`RuntimeError`.
- **`to_dict()` / `from_dict()` on all dataclasses** — complements `serde.py`; they are not redundant. See [invariants](docs/agent-context/invariants.md#serialization-design).
- **Deterministic by default** — tie-break by ID, sorted keys. No randomness in core pipelines.
- **No wildcard imports** — never use `from contextweaver import *`.
- **Event log is append-only** — mutate only via `InMemoryEventLog.append()`.

## Coding Style

- **Python ≥ 3.10** — use `X | Y` union syntax, `match` statements where appropriate.
- **Type hints** on all public functions and methods.
- **Google-style docstrings** on all public classes and functions.
- **100-character line length** (enforced by ruff).
- **≤ 300 lines per module** — exempt: `types.py`, `envelope.py`, `__main__.py`.
- **Minimal core runtime dependencies.** The core install pulls only `tiktoken`, `PyYAML`, and `rank-bm25` — each is broadly used in GenAI stacks and unblocks default behaviour. Adding a new core dependency requires explicit justification: broad ecosystem use, small wheel, and a default the library would otherwise have to approximate. Heavy or runtime-specific packages (CLI, OpenTelemetry, fuzzy retrieval, ANN, NetworkX, FastMCP, LangChain) live under `[project.optional-dependencies]` and are loaded via guarded imports.

## Testing

- Tests in `tests/test_<module>.py` — one file per module.
- `pytest.mark.asyncio` for async tests (`asyncio_mode = "auto"` is set globally).
- Do not mock internal modules — use real in-memory implementations.

## Path Conventions

**`store/`** — Protocols are backend-agnostic (must not import backend-specific libraries). Concrete implementations may import backend libs. Must implement the protocol from `protocols.py`. Data is append-only / immutable-after-write.

**`adapters/`** — Pure stateless converters. External format parsing must not leak into core. May import optional external libraries at the adapter boundary only.

**`context/`** — Async-first. All new code should be async with `_sync` wrappers.

**`routing/`** — Sync-only. Pure computation (DAG traversal, beam search). Do not make async.

**Sensitivity (`context/sensitivity.py`)** — Security-grade code. Extra review scrutiny required. Never weaken defaults. Treat changes like security-sensitive code.

## Things That Must Not Be "Simplified"

1. **Protocol-based store design** — the protocol layer exists for backend extensibility. Do not collapse protocols into concrete classes.
2. **`dependency_closure` pipeline stage** — if a selected item has `parent_id`, the parent must be included. Removing it produces incoherent context (tool results without their tool calls).
3. **`serde.py` + per-class `to_dict`/`from_dict`** — complementary, not redundant. `serde.py` provides shared primitives; per-class methods handle class-specific serialization. Do not consolidate.

See [docs/agent-context/invariants.md](docs/agent-context/invariants.md) for the full invariants list and rationale.

## Debugging Tips

1. `make lint` — check for style and import errors.
2. `make type` — check for type errors.
3. `make test` — run the test suite.
4. Check `BuildStats` fields to understand what the context engine dropped and why.
5. Use `ContextManager.artifact_store.list_refs()` to inspect intercepted tool outputs.
6. Enable `logging.DEBUG` on `contextweaver.context` to trace pipeline stages (candidate counts, scores, drops, budget usage).
7. Enable `logging.DEBUG` on `contextweaver.routing` to trace beam search expansions and scoring.

## Adding a Feature

1. Identify the relevant module, modify it, add tests in `tests/test_<module>.py`.
2. Run `make ci` to verify (all 6 targets must pass).
3. Update `CHANGELOG.md` and add docstrings to new public APIs.
4. Update agent-facing docs and examples if the pipeline or public API changed.

For the full workflow and definition of done, see [docs/agent-context/workflows.md](docs/agent-context/workflows.md).

## Common Pitfalls

See [docs/agent-context/lessons-learned.md](docs/agent-context/lessons-learned.md) for durable recurring mistakes and how to avoid them.

## Documentation Map

| File | Role |
|---|---|
| `AGENTS.md` (this file) | Primary shared source of truth for all agents |
| `docs/agent-context/architecture.md` | Non-obvious architectural guidance and tradeoffs |
| `docs/agent-context/workflows.md` | Authoritative commands, sequencing, definition of done |
| `docs/agent-context/invariants.md` | Hard constraints and forbidden shortcuts |
| `docs/agent-context/lessons-learned.md` | Failure-capture workflow and durable lessons |
| `docs/agent-context/review-checklist.md` | Self-check and review gates |
| `docs/architecture.md` | Canonical architecture reference (full pipeline detail, diagrams) |
| `docs/concepts.md` | Core concept glossary (types, subsystems, phases) |
| `CONTRIBUTING.md` | Human contributor guide |

When architecture details conflict, `docs/architecture.md` is the canonical reference.

## Update Policy

- Update `AGENTS.md` when shared rules, conventions, or the module map change.
- Update `docs/agent-context/` files when their specific topic area changes.
- Any PR that changes the pipeline, public API, or project conventions must include doc updates.
- If two docs disagree, `AGENTS.md` is authoritative for agent guidance; `docs/architecture.md` is authoritative for architecture detail.
- See [docs/agent-context/workflows.md](docs/agent-context/workflows.md) for documentation governance rules.

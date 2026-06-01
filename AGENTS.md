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
| `protocols.py` | Protocol interfaces: `TokenEstimator`, `EventHook`, `Summarizer`, `Extractor`, `RedactionHook`, `MemorySource`, `Labeler`, `Retriever`, `Reranker`, `ClusteringEngine`, `RoutingScoreProvider` (store protocols re-exported from `store/protocols.py`) |
| `store/protocols.py` | Store-layer protocols: `EventLog`, `ArtifactStore`, `EpisodicStore`, `FactStore` |
| `exceptions.py` | Custom exception hierarchy (all errors inherit `ContextWeaverError`) |
| `_utils.py` | Text similarity primitives: `tokenize()`, `jaccard()`, `TfIdfScorer` |
| `_version.py` | Single-source version derived from `importlib.metadata`; fallback `"0.0.0+local"` |
| `_demos.py` | Demo logic for the CLI `demo` subcommand (exempt from `print()` rule) |
| `serde.py` | Serialisation helpers for `to_dict` / `from_dict` |
| `store/` | In-memory data stores: `EventLog`, `ArtifactStore`, `EpisodicStore`, `FactStore`, `StoreBundle` |
| `store/_sqlite_base.py` | Shared SQLite connection + migration scaffolding (WAL, `foreign_keys=ON`, `_contextweaver_schema_version` table). Reused by every SQLite-backed store (issue #174). |
| `store/sqlite_event_log.py` | `SqliteEventLog` — first persistent `EventLog` backend; single-process, sync, append-only, schema-versioned (issue #223). |
| `store/json_file_artifacts.py` | `JsonFileArtifactStore` — filesystem `ArtifactStore` backend; `{handle}.data` + `{handle}.json` per artifact, re-instantiable against an existing directory (issue #42). |
| `summarize/` | `SummarizationRule`, `RuleEngine`, `extract_facts()` |
| `context/` | Full context pipeline, sensitivity enforcement, view registry, `ContextManager` |
| `context/ingest.py` | Tool-result ingestion helpers (extracted from `manager.py` to honor the <=300 line guideline) |
| `context/memory_types.py` | `MemoryEntry` dataclass + `PHASE_SCOPE_PREFERENCES` constants for phase-aware memory ingestion (issue #293). |
| `context/memory_fixture.py` | `JsonFixtureMemorySource` — deterministic stdlib fixture adapter implementing the `MemorySource` Protocol from `protocols.py` (issue #293). |
| `context/memory_source.py` | `memory_entries_to_context_items` / `select_memory_for_phase` helpers that materialise memory entries into budgeted `memory_fact` candidates (issue #293). |
| `context/handoff_types.py` | `HandoffEntry` + `SessionHandoffPack` dataclasses and canonical handoff category constants (issue #294). |
| `context/handoff.py` | `build_session_handoff_pack` / `render_handoff_pack` — deterministic, budget-aware, sensitivity- and firewall-respecting session continuity snapshot (issue #294). |
| `context/explanation.py` | `ContextBuildExplanation` + `CandidateExplanation` opt-in debug surface returned by `ContextManager.build(..., explain=True)` (issue #291). Sister to `routing/explanation.py` on the routing side. |
| `routing/` | `Catalog`, `ChoiceGraph`, `TreeBuilder`, `Router` (beam search), card renderer |
| `routing/filters.py` | Pre-scoring helpers: `filter_items()`, `augment_query()`, `suggest_clarifying_question()` (issues #14, #22, #112, #116) |
| `routing/manifest.py` | `GraphManifest` + `compute_catalog_hash()` for graph metadata and cache invalidation (issue #48, #15) |
| `routing/normalizer.py` | `CatalogNormalizer` + `NormalizationReport` for catalog metadata hygiene (issue #44) |
| `routing/registry.py` | `EngineRegistry` and bundled `TfIdfRetriever` / `NoOpReranker` / `JaccardClusteringEngine` defaults (issue #47) |
| `routing/trace.py` | `RouteTrace` + `TraceStep` structured routing audit (issue #51) |
| `routing/explanation.py` | `RouteResult.explanation()` Markdown / dict rendering (issue #226) |
| `routing/pipeline.py` | `RoutingPipeline` composer — explicit retrieve → rerank → navigate → pack stages (issue #56) |
| `routing/navigator.py` | `BeamSearchNavigator` (lifted from `router.py`) + `rank_collected` (issue #56) |
| `routing/packer.py` | `DefaultCardPacker` wrapping `make_choice_cards` for the pipeline pack stage (issue #56) |
| `routing/history.py` | `RouteHistory` dataclass + `adjust_scores` (history-aware re-routing, issue #27) |
| `routing/feedback.py` | Optional feedback-aware routing scores (issue #318): `ExecutionFeedback` (contextweaver-native, **not** a weaver-spec type), `DeterministicScoreProvider` (default no-op), `FeedbackAwareScoreProvider`, `aggregate_feedback`. Plugs into `Router(score_provider=...)`; default `None` keeps routing deterministic. |
| `extras/embeddings.py` | `SentenceTransformerBackend` + `HybridEmbeddingRetriever` + `HashingEmbeddingBackend` (re-exported) behind the `[embeddings]` extra (issue #8) |
| `extras/embeddings_hashing.py` | `HashingEmbeddingBackend` — stdlib-only deterministic `EmbeddingBackend` using blake2b hashing trick; no extras required (issue #266) |
| `_schema_gen.py` | Dataclass → JSON Schema (Draft 2020-12) generator + `make schemas-check` engine (issue #225) |
| `routing/tool_id.py` | Canonical `tool_id` grammar (`parse_tool_id` / `format_tool_id` / `compute_hash8`) per `docs/gateway_spec.md` §1 |
| `routing/path.py` | `tool_browse` path-navigation grammar (`parse_path` / `resolve_path`) per `docs/gateway_spec.md` §3 |
| `routing/hydration.py` | Public schema-hydration helpers — `SchemaSource` (from raw dict / JSON file / MCP tools-list), `hydrate_with_schema`, `lazy_schema_resolver`. Reference architectures use these to resolve a tool's full input schema from a sidecar source rather than hand-rolling a `_FULL_SCHEMAS` dict. Inline `args_schema` on the catalog item wins; sidecar only fills empties. Issue #261. |
| `adapters/` | MCP, FastMCP, A2A, weaver-spec, CrewAI, Pydantic AI, smolagents, Agno protocol adapters + MCP proxy / gateway runtime + provider-message ingestion helpers for OpenAI / Anthropic / Gemini chat histories (issues #13, #28, #29, #34, #193, #194, #219, #222, #272, #274, #275) |
| `adapters/chainweaver.py` | ChainWeaver flow-export → `SelectableItem(kind="flow")` import (`chainweaver_flow_to_selectable`, `chainweaver_flows_to_catalog`, `load_chainweaver_export`, issue #334). Pure data; no ChainWeaver dependency. Preserves name/description/input+output schemas; stamps `metadata["runtime"]="chainweaver"` + flow id/version. |
| `adapters/crewai.py` | CrewAI `BaseTool` (or equivalent plain-dict shape) ↔ `SelectableItem` (`crewai_tool_to_selectable`, `crewai_tools_to_catalog`, `infer_crewai_namespace`, `load_crewai_catalog`, issue #193) |
| `adapters/pydantic_ai.py` | Pydantic AI `Tool` ↔ `SelectableItem` and `ModelMessage` ↔ `ContextItem` lossless round-trip (`pydantic_ai_tool_to_selectable`, `pydantic_ai_tools_to_catalog`, `load_pydantic_ai_catalog`, `from_/to_pydantic_ai_messages`, issue #272) — heavy decode/encode helpers live in `adapters/_pydantic_ai_messages.py` |
| `adapters/smolagents.py` | Hugging Face smolagents `Tool` ↔ `SelectableItem` and `MultiStepAgent.memory.steps` → `ContextItem`s (`smolagents_tool_to_selectable`, `smolagents_tools_to_catalog`, `load_smolagents_catalog`, `from_smolagents_agent`, issue #274) |
| `adapters/agno.py` | Agno (formerly Phidata) `Function` / `Toolkit` ↔ `SelectableItem` and `AgentSession` → `ContextItem`s (`agno_tool_to_selectable`, `agno_tools_to_catalog`, `load_agno_catalog`, `from_agno_session`, issue #275) |
| `adapters/proxy_runtime.py` | `ProxyRuntime` shared core + `ExposureMode` enum + `UpstreamCall` Protocol (issue #29) |
| `adapters/mcp_gateway.py` | Two-tool gateway dispatch (`tool_browse` + `tool_execute` + `tool_view`, issues #28 / #34) |
| `adapters/mcp_proxy.py` | Transparent proxy dispatch (stripped `tools/list` + `tool_hydrate` + `tool_execute`, issue #13) |
| `adapters/mcp_upstream.py` | Concrete `UpstreamCall` adapters (`StubUpstream`, `McpClientUpstream`, `MultiplexUpstream`) |
| `adapters/mcp_gateway_server.py` | Bind `mcp_gateway` onto `mcp.server.Server` over stdio (issue #28) |
| `adapters/mcp_proxy_server.py` | Bind `mcp_proxy` onto `mcp.server.Server` over stdio (issue #13) |
| `adapters/gateway_error.py` | Structured `GatewayError` (codes + §3.4 wire shape) |
| `adapters/openai_messages.py` | OpenAI Chat Completions `messages` ↔ `ContextItem` round-trip (`from_/to_openai_messages`, issue #219) |
| `adapters/anthropic_messages.py` | Anthropic Messages API `messages` ↔ `ContextItem` round-trip (`from_/to_anthropic_messages`, issue #222) |
| `adapters/gemini_contents.py` | Google Gemini `contents[]` ↔ `ContextItem` round-trip (`from_/to_gemini_contents`, issue #222) |
| `extras/otel.py` | OpenTelemetry GenAI integration (`OTelEventHook` — `invoke_agent` / `execute_tool` spans + GenAI SemConv attributes, gated behind the `[otel]` extra, issue #224). |
| `extras/memory/` | External-memory backend adapters that implement `EpisodicStore` / `FactStore` against an existing long-lived memory deployment without widening the Protocols (issue #195). |
| `extras/memory/mem0.py` | `Mem0EpisodicStore` + `Mem0FactStore` — wrap a `mem0.Memory` instance scoped by `user_id`; writes go through `Memory.add(infer=False)` and items are stamped with `cw_episode_id` / `cw_fact_id` metadata for canonical-ID resolution. Gated behind the `[mem0]` extra (issue #195). |
| `eval/` | Evaluation harness (issue #12): `EvalCase` / `EvalDataset` (gold datasets), `evaluate_routing` → `RoutingEvalReport` (top-k recall, MRR, confidence gap, beam steps), `evaluate_context` → `ContextEvalReport` (budget utilisation + token savings vs naive concat). Pure-stdlib, deterministic; backs the `eval` CLI subcommand. |
| `__main__.py` | CLI: 10 subcommands (`demo`, `build`, `route`, `print-tree`, `init`, `ingest`, `replay`, `stats`, `budget-check`, `eval`) plus the `mcp` Typer sub-app (`mcp serve`, [experimental] stdio MCP gateway/proxy entrypoint; issues #243/#246). Typer + Rich (both core deps as of v0.5, issue #221). |
| `_mcp_cli.py` | Backs the `mcp` Typer sub-app mounted from `__main__.py`. Hosts `mcp serve` (stdio MCP gateway or transparent proxy) and the catalog loader that accepts both native contextweaver and raw MCP `tools/list` shapes. Marked `[experimental]` in `--help` for v0.9. Uses `typer.echo(..., err=True)` for stderr output (library-code `print()` is forbidden). |
| `data/` | Packaged data files shipped inside the wheel via `[tool.setuptools.package-data]`. Exposes `gateway_catalog_path()` (resolves `mcp_gateway_catalog.yaml` to a concrete `Path` for both editable installs and zipped wheels — falls back to a persistent cache under `tempfile.gettempdir()/contextweaver/` for zipimport). Issue #264. |
| `examples/recipes/` | MCP-client integration recipes: `serve_gateway.py` launcher + `claude_desktop_config.json` / `copilot_mcp.json` example configs referenced from `docs/recipes/` (issues #278, #279). |

## Pipelines (summary)

**Context Engine** — 8 stages:

1. `generate_candidates` → 2. `dependency_closure` → 3. `sensitivity_filter` →
4. `apply_firewall` → 5. `score_candidates` → 6. `deduplicate_candidates` →
7. `select_and_pack` → 8. `render_context`

**Routing Engine** — 4 stages:

1. `Catalog` → 2. `TreeBuilder` → 3. `Router` (beam search) → 4. `ChoiceCards`

The `Router` itself composes a four-stage `RoutingPipeline` internally
(retrieve → rerank → navigate → pack, issue #56).  Each stage is
swappable via the `EngineRegistry` or by passing a custom
`RoutingPipeline` to `Router(pipeline=...)`.  History-aware re-routing
(`Router.route(history=...)`, issue #27) and the optional embedding
retriever (`Router(embedding_backend=...)`, issue #8) plug into this
same pipeline contract.

For full pipeline descriptions and design rationale, see [docs/agent-context/architecture.md](docs/agent-context/architecture.md).

## Key Types

| Type | Purpose |
|---|---|
| `SelectableItem` | Unified tool/agent/skill/flow/internal item (`kind="flow"` = external multi-step capability, e.g. a ChainWeaver flow). Alias: `ToolCard` (use `SelectableItem` in code). |
| `ContextItem` | Event log entry with `parent_id` for dependency closure |
| `ResultEnvelope` | Processed tool output: summary + facts + artifacts + views |
| `ContextPack` | Rendered prompt + stats from a context build |
| `BuildStats` | What was kept, dropped, and why — diagnostic output of every build |
| `ChoiceCard` | LLM-friendly compact card (never includes full schemas) |
| `RoutingDecision` | Routing output shaped for weaver-spec interop (id, choice_cards, timestamp, selection). `choice_cards` is a flat list of CW 1:1 cards; for schema-valid spec JSON, go through `adapters.weaver_contracts.to_weaver_routing_decision()`. Build with `RouteResult.to_routing_decision(...)`. |
| `ChoiceGraph` | Bounded DAG for routing, serializable, validated on load |
| `GraphManifest` | Build-time metadata attached to every routing graph (hash, seed, engine versions, timestamp) |
| `RouteTrace` | Always-populated structured audit of a routing call; per-step expansions opt-in via `debug=True` |
| `EngineRegistry` | Pluggable registry for `Retriever`, `Reranker`, `ClusteringEngine` slots |
| `Mode` | Determinism mode (`strict` / `seeded` / `adaptive` placeholder) on `ProfileConfig` |
| `MaskRedactionHook` | Built-in redaction hook for sensitivity enforcement |
| `HydrationResult` | Result of hydrating a tool call with context |
| `ViewRegistry` | Maps content-type patterns to view generators for progressive disclosure |
| `ProxyRuntime` | Shared core for MCP proxy (#13) and gateway (#28) modes — owns upstream catalog, per-session `ContextManager`, browse / execute / view dispatch |
| `ExposureMode` | `TRANSPARENT` (#13) vs `GATEWAY` (#28) for `ProxyRuntime` |
| `UpstreamCall` | Transport-agnostic Protocol over upstream MCP fan-out (used by `ProxyRuntime`) |
| `GatewayError` | Structured error payload (§3.4) returned from every gateway/proxy meta-tool |
| `ToolIdParts` | Destructured canonical `tool_id` (namespace / name / version / hash8) |

**Vocabulary notes:**
- `SelectableItem` is the canonical name. `ToolCard` is a user-facing alias — use `SelectableItem` in code and docs.
- "Context" is overloaded — can mean `ContextItem`, `ContextPack`, the pipeline, or the LLM context window. Disambiguate when unclear. See [docs/concepts.md](docs/concepts.md).
- "Firewall" here means context firewall (prevents large outputs from consuming the token budget), not a security firewall.

## Commands

```bash
make fmt      # ruff format src/ tests/ examples/ scripts/
make lint     # ruff check src/ tests/ examples/ scripts/
make type     # mypy src/
make test     # python -m pytest --cov=contextweaver --cov-report=term-missing -q
make example  # run all example scripts (includes architectures via the umbrella target)
make architectures  # run reference architecture scripts under examples/architectures/
make demo     # python -m contextweaver demo
make ci       # fmt + lint + type + test + example + demo
make docs     # mkdocs build --clean (docs site)
make docs-serve  # mkdocs serve (live preview)
make benchmark        # run benchmark harness (non-gating; writes benchmarks/results/latest.json)
make benchmark-matrix # benchmark + per-backend × per-size matrix (#208) and per-namespace breakdown (#209)
make smoke-eval       # optional, non-gating smoke-evaluation over fixed fixtures (#331); deterministic, credential-free
make scorecard        # render benchmarks/scorecard.md from benchmarks/results/latest.json
make scorecard-check  # verify scorecard.md is up to date (exits non-zero on drift)
make schemas         # regenerate schemas/ + docs/schemas/v0/ (issue #225)
make schemas-check    # verify published schemas match dataclasses (gating, in `make ci`)
make sweep-scoring    # weight sweep for ScoringConfig (#214); writes benchmarks/sweep_scoring.md
make context-rot       # render the context-rot demo: benchmarks/results/context_rot.json + docs/assets/context_rot.svg (#349)
make context-rot-check # verify context_rot.svg matches its committed JSON (gating in CI; exits non-zero on drift)
make readme-version-check  # verify README version references match pyproject.toml (gating in CI; #347)
make llms        # regenerate llms.txt and llms-full.txt from canonical docs
make llms-check  # verify llms.txt and llms-full.txt are up to date (exits non-zero on drift)
make weaver-conformance  # round-trip + JSON-Schema validate the weaver-spec adapter (CI gating, fetches schemas)
```

Run `pre-commit install` once after cloning to activate git hooks
(ruff format + check + file hygiene on every commit).

For command-selection rules and sequencing, see [docs/agent-context/workflows.md](docs/agent-context/workflows.md).

## Hard Rules

These are auto-reject in review. No exceptions.

1. **No `print()` in library code.** Use hooks or logging. `__main__.py` and `_demos.py` (CLI) are exempt.
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
- **≤ 300 lines per module** — exempt: `types.py`, `envelope.py`, `__main__.py`,
  `_mcp_cli.py` (experimental Typer sub-app; size is dominated by Typer
  parameter declarations + docstrings and is expected to shrink once
  `mcp serve` graduates from `[experimental]` to stable), and `_demos.py`
  (CLI demo-output module — print-heavy walkthrough scripts backing the
  `demo` subcommand, same rationale as `__main__.py`).
- **Core runtime dependencies.** The core install pulls `tiktoken`, `PyYAML`, `rank-bm25`, plus `mcp` and `jsonschema` (added when the proxy / gateway runtimes landed — both are load-bearing for `docs/gateway_spec.md` §4.4 schema validation and the MCP transport binding).  Adding *another* core dependency requires explicit justification: broad ecosystem use, small wheel, and a default the library would otherwise have to approximate.  Heavy or runtime-specific packages (CLI, OpenTelemetry, fuzzy retrieval, ANN, NetworkX, FastMCP, LangChain) live under `[project.optional-dependencies]` and are loaded via guarded imports.

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
5. **If the feature can move recall@k / drops / dedup / token counts**: follow
   [`.github/prompts/add-eval.prompt.md`](.github/prompts/add-eval.prompt.md)
   to extend the gold set or scenarios and run `make benchmark-matrix &&
   make scorecard`. CI will post a sticky benchmark-delta comment on the PR.

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

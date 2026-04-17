# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- `make test` now runs `pytest --cov=contextweaver --cov-report=term-missing -q` (non-gating coverage report); updated `AGENTS.md`, `docs/agent-context/workflows.md`, and `.claude/CLAUDE.md` to match (#165)
- Coverage config: removed redundant `omit` pattern (already excluded by `source` scope), added `branch = true` for branch coverage visibility, tightened `"if __name__"` exclusion regex to `"if __name__ == ['"]__main__['"]"` (#165)


### Removed

- **[breaking]** `ContextPolicy.ttl_behavior` field removed from `config.py` (#65).
  The field was declared but never read by any pipeline stage — `ContextItem` has no TTL
  field and no pipeline stage acted on it, so silently ignored config eroded trust.
  TTL/eviction support is tracked separately in #67.

  **Migration:** remove `ttl_behavior` from any `ContextPolicy(ttl_behavior=...)` calls
  or `"policy": {"ttl_behavior": "drop"}` entries in `contextweaver.json`.
  No behaviour changes — the field had no effect in any prior release.
  If you need to forward-compat a shared config dict, use the existing `extra` catch-all:
  `ContextPolicy(extra={"ttl_behavior": "drop"})`.

### Added
- `StoreBundle.from_dict()` — symmetric counterpart to `to_dict()`, enabling full round-trip serialization of store bundles (#66)
- `InMemoryArtifactStore.from_dict()` — restores the metadata index (refs) from a serialized dict; raw artifact bytes are intentionally excluded from serialization and must be repopulated via `put()` after loading (#66)
- `DuplicateItemError(ContextWeaverError)` — new public exception raised when an item
  with a duplicate ID is appended to an append-only store (e.g. `InMemoryEventLog`); exported
  from the top-level `contextweaver` package (#64)
- `docs/troubleshooting.md` — new end-to-end troubleshooting guide with 10 common
  issues, debugging techniques, performance optimisation table, and 12-entry FAQ (#82)
- README FAQ section (5 entries) and link to troubleshooting guide
- Benchmark harness for routing and context pipeline (#119)
  - `benchmarks/routing_gold.json` — 50 queries mapped to expected tool IDs across all 8 catalog namespaces
  - `benchmarks/benchmark.py` — standalone script computing routing metrics (precision@k, recall@k, MRR, p50/p95/p99 latency) and context pipeline metrics (prompt_tokens, budget_utilization_pct, included/dropped/dedup counts, artifacts_created, avg_compaction_ratio)
  - Tests 3 catalog sizes: 50, 83 (full natural pool), and 1000 (synthetic extension); catalog sizes now generated with explicit `n` so each size reflects the intended sampling without synthetic contamination
  - 3 scenario JSONL files in `benchmarks/scenarios/` (short_conversation, long_conversation, large_catalog)
  - `make benchmark` target; CI runs benchmark as a non-gating informational step
  - JSON results written to `benchmarks/results/latest.json`; path git-ignored
  - Stdlib-only, deterministic (seeded), no new runtime dependencies
- Named configuration presets in `config.py` (#133)
  - `RoutingConfig` dataclass bundling `beam_width`, `max_depth`, `top_k`, `confidence_gap`, `max_children`; includes `routing_kwargs()`, `to_dict()`, `from_dict()`
  - `ProfileConfig` dataclass bundling `budget`, `policy`, `scoring`, `routing`; includes `from_preset()`, `to_dict()`, `from_dict()`
  - Three named presets: `"fast"` (low-latency), `"balanced"` (general-purpose), `"accurate"` (high-recall)
  - `Router` now accepts a keyword-only `routing_config: RoutingConfig` parameter that overrides individual beam-search kwargs
  - `ConfigError` exception added to `contextweaver.exceptions` for invalid config/preset names
- FastMCP Catalog bridge adapter in `adapters/fastmcp.py` (#114)
  - `fastmcp_tool_to_selectable()` — convert FastMCP tool definitions to `SelectableItem`
  - `fastmcp_tools_to_catalog()` — batch-convert tool definitions into a populated `Catalog`
  - `load_fastmcp_catalog()` — async live discovery from any FastMCP server source
  - `infer_fastmcp_namespace()` — 2-segment namespace inference matching FastMCP composition convention
  - `contextweaver[fastmcp]` optional extra (`fastmcp>=2.0`)
  - Example recipe in `examples/fastmcp_adapter_demo.py`
- End-to-end four-phase runtime loop example in `examples/full_agent_loop.py` (#24)
- Runtime loop guide with flow diagram and phase guidance in `docs/guide_agent_loop.md` (#24)
- LangChain memory replacement example in `examples/langchain_memory_demo.py` (#170) — demonstrates replacing `InMemoryChatMessageHistory` with phase-specific budgets and the context firewall using a deterministic mock LLM and real `langchain-core` objects
- `llms.txt` — structured documentation index for AI tools (llmstxt.org convention) with Docs,
  Agent Context, API, and Examples sections; includes `docs/agent-context/` as a dedicated
  section for AI contributor guidance
- `llms-full.txt` — single-file concatenation of all documentation (README + docs/* +
  docs/agent-context/*) with `<!-- FILE: ... -->` section markers and a generated-file header
  documenting regeneration instructions; relative links in the embedded quickstart section
  rewritten to root-relative paths
- MCP annotation security documentation (#21): `mcp_tool_to_selectable()` docstring now
  includes a Google-style `Warning:` section noting that annotations are untrusted hints;
  `docs/integration_mcp.md` gains a "Security Considerations" section with annotation mapping
  table and an "Authorization status" subsection clarifying contextweaver has no current
  authorization mechanism (`CapabilityToken` is planned, see issue #20)

### Changed
- `StoreBundle` moved from `store/__init__.py` to `store/bundle.py`; re-exported transparently — public API unchanged (#66)
- `InMemoryEventLog.append()` now raises `DuplicateItemError` instead of bare `ValueError`
  on duplicate item ID — callers catching `ValueError` must migrate to `DuplicateItemError`
  or the `ContextWeaverError` base class (#64)
- `InMemoryArtifactStore.drilldown()` now raises `ContextWeaverError` instead of bare
  `ValueError` for unknown selector types — callers catching `ValueError` must migrate to
  `ContextWeaverError` (#64)
- `Router` default `top_k` changed from 20 → 10 to align with the `"balanced"` preset (#133)
- README now includes a "Runtime Loop (4 Phases)" section and references the new example/guide
- `make example` now runs `examples/full_agent_loop.py` and `examples/langchain_memory_demo.py`
- `pyproject.toml` now includes a `[langchain]` extras group (`langchain-core>=0.3`) for LangChain integration examples
- CI now installs `.[dev,langchain]` so `make example` runs the LangChain demo end-to-end
- README: corrected CI trigger wording from "on every push" to "on every pull request and on pushes to `main`" (#158)
- README: fixed "Async-first context engine" rationale — wording now accurately reflects the async-compatible (not non-blocking) API (#158)
- README: aligned framework guide status labels — both "Framework Integrations" and "Framework Agnostic" tables now use `"Guide (v0.2)"` consistently (#158)
- README: resolved internal inconsistency in versioning policy — deprecation contract now explicitly states removals happen in a later major release, not after a minor-version warning alone (#158)

### Fixed
- `_strip_namespace_prefix()` now also strips `{namespace}.` and `{namespace}/` prefixes,
  preventing the namespace from appearing verbatim in the tool's display name for
  dot- and slash-delimited FastMCP names (e.g. `"github.create_issue"` → `name="create_issue"`) (#177, review)
- `fastmcp_tool_to_selectable()` now normalizes `meta` values before merging into
  `SelectableItem.metadata`: `set`/`frozenset` are coerced to sorted lists and `tuple` to
  lists, ensuring `to_dict()` / JSON serialization never fails on FastMCP metadata (#177, review)
- Auto-generated API reference documentation site using MkDocs + Material + mkdocstrings (#110)
  - `mkdocs.yml` — site configuration with Material theme, auto-nav, and mkdocstrings
  - `docs/gen_ref_pages.py` — build-time script that walks `src/contextweaver` and emits one reference page per public module; new modules are picked up automatically
  - `docs/index.md` — public landing page for the docs site
  - `[docs]` extras group in `pyproject.toml` (`mkdocs`, `mkdocs-material`, `mkdocstrings[python]`, `mkdocs-gen-files`, `mkdocs-literate-nav`, `mkdocs-section-index`)
  - `make docs` builds the site; `make docs-serve` starts a local preview server
  - `.github/workflows/docs.yml` — publishes to GitHub Pages on every push to `main`; CI workflow permissions are scoped per-job (build: `contents: read`, deploy: `pages: write` + `id-token: write`)
  - README now links to `https://dgenio.github.io/contextweaver`
  - `AGENTS.md` and `docs/agent-context/workflows.md` updated to document `make docs` / `make docs-serve` targets
- `mkdocs.yml` `edit_uri` corrected from `edit/main/docs/` to `edit/main/` so that auto-generated API reference "Edit" buttons resolve to `src/contextweaver/*.py` rather than the nonexistent `docs/src/...` path
- `docs/gen_ref_pages.py` dunder-module handling (`__init__`, `__main__`) now runs before the private-name filter so package `__init__.py` docstrings are rendered as package index pages in the API reference; the private filter now correctly excludes only non-dunder private modules and package directories
- `docs/gen_ref_pages.py` module walk restricted to `src/contextweaver` (matches docstring; prevents accidental inclusion of future sibling packages under `src/`)
- Corrected all runnable snippets in `docs/troubleshooting.md` to match actual APIs:
  - `ArtifactStore.get()` returns `bytes`, not an object with `.content`
  - `ArtifactRef` field is `handle`, not `ref_id`
  - `EventLog` exposes `all()` / `filter_by_kind()` / `count()`, not `list()`
  - `FactStore.get_by_key(key)` is the correct API for key-based fact lookup
  - `EpisodicStore` items use `episode_id` / `summary`, not `id` / `text`
  - `ContextPack` has no `token_count`; totals computed from `pack.stats`
  - `await mgr.build()` still runs the synchronous pipeline; recommend
    `asyncio.to_thread(mgr.build_sync, ...)` for true non-blocking async
  - Issue 4 diagnosis: removed non-existent `"phase_filter"` key; documented
    valid `dropped_reasons` keys (`budget`, `kind_limit`, `sensitivity`) and
    the `total_candidates == 0` vs `dropped_count > 0` distinction

## [0.1.7] - 2026-03-21

### Added
- 10-minute quickstart guide with three runnable onboarding examples in `docs/quickstart.md`

### Changed
- README now links to the dedicated quickstart guide

## [0.1.6] - 2026-03-10

### Added
- Python `logging` integration with structured events across all subsystems (#111)
  - Loggers: `contextweaver.context`, `contextweaver.routing`, `contextweaver.store`, `contextweaver.adapters`
  - DEBUG-level messages at each context pipeline stage (candidate generation, scoring, dedup, selection, firewall, sensitivity)
  - INFO-level summary messages for context builds and route completions
  - Sensitivity guard: item text content is never logged at any level
- Path-scoped Copilot instructions for `context/` and `routing/` (#95)

## [0.1.5] - 2026-03-07

### Added
- MCP structured content (`structuredContent`) support — JSON output stored as artifact with facts extracted from top-level keys
- MCP `outputSchema` support for tool definitions; `SelectableItem` now includes `output_schema` field (#102)
- MCP content types: `audio` (base64-decoded binary artifact) and `resource_link` (URI reference as `ArtifactRef`)
- Per-part content `annotations` (`audience`, `priority`) tracked in `provenance["content_annotations"]`
- PR template and YAML issue forms (`.github/`)

### Fixed
- Use `text/uri-list` MIME for `resource_link` binaries payload
- Use `validate=True` for base64 decoding in image and audio parts
- Add `isinstance` guard for `structuredContent` fact extraction and per-part annotations
- Use `is not None` for `output_schema` checks to preserve empty dict schemas
- Set `resource_link` `size_bytes` to actual URI length
- Widen `structured_content` annotation to `Any` for MCP spec compliance

### Changed
- Extracted `_decode_binary_part` helper for image/audio binary decoding

## [0.1.4] - 2026-03-06

### Added
- `Summarizer` protocol in `protocols.py` — converts raw tool output into human/LLM-readable summaries
- `Extractor` protocol in `protocols.py` — extracts structured facts from raw tool output
- Pluggable `summarizer` and `extractor` parameters on `apply_firewall()` and `apply_firewall_sync()`
- `ContextManager` now accepts optional `summarizer` and `extractor` at construction, wired through `build()` / `build_sync()`

### Fixed
- `infer_namespace()` now guards against empty prefixes caused by leading separators (e.g. `.foo` or `/bar`)

## [0.1.3] - 2026-03-05

### Added
- `infer_namespace()` helper in MCP adapter — infers namespace from tool name prefixes (dot, slash, underscore) (#43)
- Progressive disclosure for tool results: view registry + drilldown loop (#17)
- `ViewRegistry` class in `context/views.py` — maps content-type patterns to `ViewSpec` generators
- Built-in view generators for `application/json`, `text/csv`, `text/plain`, and binary/image content
- `generate_views()` function for auto-generating `ViewSpec` entries from artifact data
- `drilldown_tool_spec()` helper — generates a `SelectableItem` exposing drilldown as an agent-callable tool
- `ContextManager.drilldown()` / `drilldown_sync()` — agent-facing wrapper for `ArtifactStore.drilldown()` with optional context injection
- `ContextManager.view_registry` property for accessing/extending the view registry
- Auto-generated `ViewSpec` entries during `ingest_tool_result()` (both large and small outputs)
- Auto-generated `ViewSpec` entries during `apply_firewall()` via view registry
- Content-type detection heuristics for generic `application/octet-stream` artifacts
- Small tool outputs now stored in artifact store with `artifact_ref` for drilldown support

### Changed
- `mcp_tool_to_selectable()` now uses `infer_namespace()` instead of hardcoding `namespace="mcp"`

## [0.1.2] - 2026-03-04

### Added
- Sensitivity enforcement in context pipeline: items at or above `ContextPolicy.sensitivity_floor` are dropped or redacted
- `ContextItem.sensitivity` field (default: `Sensitivity.public`)
- `ContextPolicy.sensitivity_action` field (`"drop"` or `"redact"`)
- `MaskRedactionHook` — built-in redaction hook replacing text with `[REDACTED: {sensitivity}]`
- `apply_sensitivity_filter()` function in `context/sensitivity.py`
- `register_redaction_hook()` for user-extensible redaction hooks
- `BuildStats.dropped_reasons["sensitivity"]` tracks sensitivity-dropped item count
- `.pre-commit-config.yaml` with ruff format, ruff check --fix, and standard file hygiene hooks

### Fixed
- Validate `sensitivity_action` to reject unknown values
- Use accumulation pattern for `dropped_reasons["sensitivity"]`
- Adjust `total_candidates` for sensitivity drops in `BuildStats`

## [0.1.1] - 2026-03-03

### Added
- `Catalog.hydrate(tool_id)` returns a `HydrationResult` with full schema, examples, and constraints
- `HydrationResult` dataclass in `envelope.py` with `to_dict()` / `from_dict()`
- `ContextManager.build_call_prompt()` / `build_call_prompt_sync()` for `Phase.call` prompts with schema injection
- `SelectableItem.examples` and `SelectableItem.constraints` fields
- `ContextManager.ingest_mcp_result()` / `ingest_mcp_result_sync()` for one-call MCP result ingestion with artifact persistence

### Changed
- **Breaking:** `mcp_result_to_envelope()` now returns `(ResultEnvelope, dict, str)` tuple — envelope, extracted binary data, and full untruncated text

## [0.1.0] - 2026-03-03

### Added
- Full CLI implementation: all 7 subcommands (demo, build, route, print-tree, init, ingest, replay) with real handlers
- MCP adapter: mcp_tool_to_selectable, mcp_result_to_envelope, load_mcp_session_jsonl
- A2A adapter: a2a_agent_to_selectable, a2a_result_to_envelope, load_a2a_session_jsonl
- Sample JSONL session files: mcp_session.jsonl, a2a_session.jsonl
- before_after.py showpiece: side-by-side token comparison WITHOUT vs WITH contextweaver
- Comprehensive test_cli.py: subprocess tests for all 7 CLI commands
- Expanded conftest.py: store_bundle, sample_context_items, sample_selectable_items, large_catalog, sample_graph, context_manager, populated_manager fixtures
- Documentation: architecture.md, concepts.md, integration_mcp.md, integration_a2a.md
- Full README.md with installation, quick start, routing, CLI, examples, and development sections

### Changed
- Version bumped to 0.1.0
- Makefile ci target now includes example and demo
- Example scripts updated: mcp_adapter_demo.py and a2a_adapter_demo.py now use real adapters

## [0.0.3] - 2026-03-02

### Added
- Routing Engine implementation: Catalog, ChoiceGraph, TreeBuilder, Router, card renderer
- KeywordLabeler with namespace detection and group summarization
- 3 tree-building strategies: namespace grouping, Jaccard clustering, alphabetical fallback
- Router with beam search, TF-IDF scoring, confidence gap, backtracking, debug trace
- ChoiceGraph with cycle detection, validation, save/load JSON, stats
- make_choice_cards() with budget/truncation, render_cards_text()
- ChoiceCard extended with kind, namespace, has_schema, score
- generate_sample_catalog with 8 namespace families (83 items)
- 105 new routing tests (338 total)

### Changed
- Split graph.py into graph_node.py (ChoiceNode) and graph_io.py (save/load)
- Topological sort uses heapq instead of list.pop(0) for O(log n) performance
- Beam/unexplored sort keys now break ties by node ID (alphabetical)
- Namespace tie-break in labeler uses alphabetical ordering (was insertion-order)

### Fixed
- Router backtrack threshold: was using beam_width (2) instead of top_k (20)
- from_dict edge leak: cycle-causing edges now discarded before raising
- Replaced assert with explicit RouteError guard in _score_node
- Added missing `from __future__ import annotations` to all __init__.py files
- Removed dead item_kinds field from stats()
- TreeBuilder validation guards for max_children and target_group_size
- cards.py: index tie-break in sort, max_desc_chars clamped to ≥4
- catalog.py docstring: 6 families → 8 families

## [0.0.2] - 2026-03-01

### Added
- RuleBasedSummarizer: concrete summarizer implementation
- StructuredExtractor: structured fact extraction (JSON, tabular, plain text)
- TiktokenEstimator: token counting via tiktoken (optional) with model name support
- EventLog: query(), children(), parent(), count(), tail() methods
- ArtifactStore: exists(), metadata(), drilldown() selectors (head, lines, json_keys, rows)
- EpisodicStore: latest(), delete() methods
- FactStore: list_keys() method
- Protocol declarations for all new EventLog and ArtifactStore methods
- Comprehensive test coverage (255 tests)

### Fixed
- Version alignment: pyproject.toml now matches __init__.py
- License alignment: pyproject.toml now uses Apache-2.0 (matches LICENSE file)
- EventLog.query() defensive copy (was leaking internal list reference)
- _EMAIL_RE regex: removed stray pipe from [A-Z|a-z] character class
- mypy override for optional tiktoken dependency

## [0.0.1] - 2026-03-01

### Added
- Initial release scaffolding
- Context Engine: phase-specific budgeted context compilation with context firewall
- Routing Engine: bounded-choice navigation via ChoiceGraph + beam search
- In-memory stores: ArtifactStore, EventLog, EpisodicStore, FactStore
- StoreBundle grouping all four stores
- Summarize sub-package: SummarizationRule, RuleEngine, extract_facts()
- Protocol definitions: TokenEstimator, EventHook, Summarizer, Extractor, RedactionHook, Labeler
- Configuration: ScoringConfig, ContextBudget, ContextPolicy
- Utility functions: tokenize(), jaccard(), TfIdfScorer
- CLI with 7 subcommands: demo, build, route, print-tree, init, ingest, replay
- MCP and A2A adapter stubs
- Example scripts: minimal_loop, tool_wrapping, routing_demo, before_after, mcp_adapter_demo, a2a_adapter_demo
- Full type annotations (PEP 561 py.typed marker)
- CI workflow (Python 3.10 / 3.11 / 3.12)

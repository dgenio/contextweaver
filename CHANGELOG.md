# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Canonical Frame-shaped ingestion seam — `ContextManager.ingest_envelope()`
  (#352).** The execution boundary (e.g. agent-kernel) firewalls and hands
  contextweaver an already-firewalled `ResultEnvelope` (the native preimage of
  a weaver-spec `Frame`); contextweaver appends a summary-only `ContextItem`
  carrying the artifact handle and does **not** re-derive firewalling from raw
  output. The raw-output APIs (`ingest_tool_result`, `ingest_mcp_result`)
  remain for standalone use but are now labelled non-canonical for spec
  compliance. New [firewall boundary doc](docs/context_firewall_boundary.md)
  explains the contextweaver-firewall vs agent-kernel-firewall split and the
  seam; weaver-spec I-05 status updated accordingly.
- **Zero-Python config-file launch for the MCP gateway (#346).**
  `contextweaver mcp serve --config gateway.yaml` reads the catalog and serve
  options (`mode`, `top_k`, `beam_width`, `cache_stable`, `name`, `version`)
  from a single JSON/YAML file; explicit CLI flags still win. The catalog
  loader now also accepts the real-MCP-server snapshot shape
  (`{"tools": [...]}`) used by the recipes. New Cursor recipe
  (`docs/recipes/cursor.md`) plus `examples/recipes/gateway_config.yaml` and
  `examples/recipes/cursor_mcp.json`. (Bridging a *live* upstream MCP server
  over stdio remains follow-up on #346.)
- **`rank_collected` is now part of the public routing API (#288).** The
  score-sort / active-filter helper is re-exported from
  `contextweaver.routing` so custom `Navigator` implementations can reuse it.
- **End-to-end quality + cost benchmark vs a competent baseline (#345).** New
  `benchmarks/e2e_quality.py` runs realistic tool-using tasks three ways —
  naive concat, a hand-built competent baseline, and contextweaver — scoring
  tool-selection accuracy, hallucinated-tool rate, end-task answer accuracy,
  prompt tokens, and estimated cost per strategy. Ships with a deterministic
  stub model (default, exercised in CI) and an opt-in real-model path
  (`CW_E2E_LLM=1` + a user-supplied `call_fn`, no LLM SDK dependency). New
  `make e2e-quality` target (non-gating) and `benchmarks/e2e/tasks.json`
  fixtures. The published real-model headline is produced from a credentialed
  maintainer run.

### Changed

- **Decomposed `ContextManager` toward the ≤300-line module guideline (#101).**
  The core build pipeline moved to `context/build.py` (`run_build_pipeline`),
  route-integrated build + history helpers to `context/route_build.py`, the
  call-phase prompt orchestration to `context/call_prompt.py`, and `drilldown`
  logic to `context/ingest.py`. `ContextManager` is now a thin orchestrator
  (`manager.py` 1090 → ~880 lines; every extracted module ≤300). No public API
  change — all methods are preserved as delegations and the full test suite
  passes unmodified. (A literal ≤300 for `manager.py` is bounded by the public
  method surface + Google docstrings, which the issue scopes as "as close as
  practical" without dropping methods or introducing mixins.)
- **Unified routing metrics into `contextweaver.eval.metrics` (#354).**
  `benchmarks/benchmark.py` and `contextweaver.eval.routing` previously
  defined `recall@k` / `reciprocal_rank` under the same names with different
  semantics (fractional recall vs boolean hit-rate). They now share one
  canonical source of truth — `recall_at_k` (classic fractional recall),
  `precision_at_k`, `reciprocal_rank` — re-exported from `contextweaver.eval`.
  The benchmark scorecard numbers are unchanged; `evaluate_routing` now reports
  fractional recall for multi-expected cases (identical for the common
  single-expected case).
- **Split `extras/memory/zep.py` into `zep.py` + `_zep_common.py`** so each
  module stays within the repo's ≤300-lines-per-module rule (PR #360 review).
  The public import path (`contextweaver.extras.memory.zep`) and its exports
  (`ZepBackendError`, `ZepEpisodicStore`, `ZepFactStore`) are unchanged.

### Fixed

- **Provider message encoders no longer emit empty-content messages.**
  `to_anthropic_messages` and `to_gemini_contents` now raise a clear
  `CatalogError` (with the offending `msg_index`) when a turn would
  serialise to empty or blank-text content, instead of letting the
  provider reject it later with an opaque
  `400 ... messages: ... must have non-empty content`. Messages that
  carry tool-use / tool-result / function-call blocks remain valid.
  OpenAI is intentionally left untouched: its Chat Completions API
  tolerates empty content and the empty-string assistant-content
  round-trip is an existing invariant (PR #230).
- **Zep backend defensively coerces scanned `tags` / `metadata`** when rebuilding
  `Episode` / `Fact` from persisted episodes: a non-list `tags` (e.g. a bare
  string, which previously iterated into characters) yields `[]`, and a non-dict
  `metadata` (which previously raised in `dict(...)`) yields `{}` (PR #360 review).
- **`LlmSummarizer` / `LlmExtractor` fallback warnings now include the underlying
  exception text**, so a degraded LLM path is diagnosable (timeout vs auth vs
  parsing) instead of opaque (PR #360 review).
- **Raise the `langgraph` floor from `>=0.2` to `>=0.2.32`** in the `[dev]`,
  `[langgraph]`, and `[langmem]` extras. The langmem adapter test imports
  `langgraph.store.memory.InMemoryStore`, which first appears in 0.2.32; the
  old `>=0.2` floor resolved to 0.2.17 under `--resolution lowest-direct` and
  failed test collection in the floor-deps CI job (PR #360).

## [0.13.4] - 2026-06-02

### Fixed

- **Restore `mcp` floor to `>=1.19.0` and `typer` floor to `>=0.16.0`**
  (both incorrectly reverted to `>=1.0` / `>=0.9` in v0.13.0).
  The `>=1.19.0` bound is the proven minimum for the live MCP gateway
  suite; the `>=0.16.0` bound is the lowest release that drives the
  Typer CLI cleanly on current click (issue #356 floor-deps commit
  `b27eb1b`).

## [0.13.3] - 2026-06-02

### Fixed

- **Restore `fastmcp` floor to `>=2.12.0`** (was incorrectly reverted to
  `>=2.0` in v0.13.0).  FastMCP `<2.12` uses the bare ``@server.tool``
  (no-call) decorator pattern which breaks at lowest-direct resolution;
  `2.12+` requires ``@server.tool()`` and is the proven minimum from the
  original floor-deps commit (`b27eb1b`).

## [0.13.2] - 2026-06-02

### Fixed

- **`pytest-asyncio` floor corrected from `>=0.23.3` to `>=0.23.8`**.
  The `0.23.3` bump in `0.13.1` still resolved to a broken version under
  `--resolution lowest-direct`.  The `>=0.23.8` bound is the proven
  minimum from the original floor-deps commit (`e8b2cfc`) and was
  accidentally reverted in a subsequent merge.

## [0.13.1] - 2026-06-02

### Fixed

- **`pytest-asyncio` floor raised from `>=0.23` to `>=0.23.8`** to fix a
  `pytest` internal `AttributeError` (`'Package' object has no attribute 'obj'`)
  that breaks test collection under `--resolution lowest-direct` / `pytest>=8.0`.
  The gating floor-deps CI job (Python 3.10) was failing with `INTERNALERROR`
  before this.  The `>=0.23.8` bound was first proven when the floor-deps job
  landed in commit `e8b2cfc` and was accidentally reverted in a later merge.

## [0.13.0] - 2026-06-02

### Added

- **Evaluation harness — `eval/` module** (#343).  `EvalCase` / `EvalDataset`
  gold-dataset types, `evaluate_routing` → `RoutingEvalReport` (top-k recall,
  MRR, confidence gap, beam steps), and `evaluate_context` →
  `ContextEvalReport` (budget utilisation + token savings vs naive concat).
  Pure-stdlib, deterministic; backs the new `eval` CLI subcommand and the
  `make smoke-eval` target.
- **Feedback-aware routing score extension point** (#318).
  `ExecutionFeedback` (contextweaver-native), `DeterministicScoreProvider`
  (default no-op), `FeedbackAwareScoreProvider`, and `aggregate_feedback`.
  Plugs into `Router(score_provider=...)`; default `None` keeps routing
  deterministic.
- **ChainWeaver flow adapter — `adapters/chainweaver.py`** (#334).
  `chainweaver_flow_to_selectable`, `chainweaver_flows_to_catalog`, and
  `load_chainweaver_export` import ChainWeaver flow exports as
  `SelectableItem(kind="flow")`.  Pure data; no ChainWeaver dependency.
  Preserves name/description/input+output schemas; stamps
  `metadata["runtime"]="chainweaver"` + flow id/version.
- **contextweaver → ChainWeaver reference architecture**
  (`examples/architectures/chainweaver_gateway/`, #353).  Demonstrates
  contextweaver routing narrowing a catalog before handing off to a
  ChainWeaver flow executor; wired into `make architectures`.
- **Python 3.10–3.14 support + library-grade dependency constraints**
  (#339, #356).  Lower-bound-only (`>=`) specifiers set to the lowest
  version actually known to work.  Gating `floor-deps` CI job proves the
  floors; non-gating weekly job (`deps-latest-weekly.yml`) runs latest +
  pre-releases as the no-upper-cap safety net.  Python 3.14 support later
  reverted (upstream adapters cap `<3.14`; see Fixed).

### Changed

- **Docs positioning** — README now leads with the MCP gateway hero; new
  context-rot live demo with drift guards (`make context-rot` /
  `make context-rot-check`).
- **Routing documented as advisory Weaver contract** (#320).

### Fixed

- **Prompt rendering of artifact handles and tool names** (#313, #308).
  Corrected the adapter-to-render contract so artifact handles and tool
  names are rendered accurately in the compiled prompt; end-to-end test
  locked.
- **README version-guard scope** — pinned to `[project]` table only; fixed
  false gate note.
- **`ExecutionFeedback.from_dict`** — rounds `float token_cost` to avoid
  JSON-serialisation drift.
- **Context-rot notebook** — catalog now grows per size so the live demo
  behaves correctly.
- **Packaging follow-up** — dropped Python 3.14 claim after upstream
  adapters capped `<3.14`; fixed floor-deps job; addressed review.
- **`CHANGELOG.md`** — switched to a runnable `uv pip install` snippet for
  the floor-deps entry.

### CI

- Weekly latest/pre-release job now fails visibly instead of silently.

## [0.12.0] - 2026-05-29

### Added

- **Catalog showcase reference architecture** — a start-here, deterministic
  example (`examples/architectures/catalog_showcase/`) that narrows a
  65-tool catalog to a 5-card shortlist, hydrates only the selected tool's
  schema, and firewalls a large result; wired into `make architectures` and
  documented at `docs/architectures/catalog_showcase.md`. (#330)
- **`contextweaver demo --scenario killer`** — the 60-second failure mode:
  100 tools + a long history + a huge tool result, contrasting a naive loop
  against contextweaver in character terms (92–99% reductions). New README
  "The 60-second failure mode" section and `docs/killer_demo.md`. (#322)
- **LangGraph agent-loop reference architecture** — contextweaver running
  *inside* a LangGraph `StateGraph`
  (`examples/architectures/langgraph_agent_loop/`): LangGraph owns control
  flow, contextweaver owns route/firewall/answer. Guarded import with a
  hand-rolled fallback so it runs without the framework; new `[langgraph]`
  extra (also in `[dev]` so CI exercises the real path).
  `docs/architectures/langgraph_agent_loop.md`. (#326)
- **Agent-safe evaluation-artifact context profile** — a context-shaping
  profile (`examples/architectures/eval_artifact_profile/`) with `ok` /
  `caution` / `high_risk` fixtures that never surfaces `V_hat` without
  support diagnostics and foregrounds caveats for high-risk artifacts, with
  runtime-asserted invariants. `docs/architectures/eval_artifact_profile.md`. (#335)

### Fixed

- **Docs accuracy follow-up to #337** — aligned the VibeGuard `--diff` shape
  between the Python subprocess and YAML CI snippets in
  `docs/cookbook.md` (both now pass `--diff origin/main...HEAD`), and
  corrected the sensitivity row in `docs/interop_skill_cards.md` so it
  matches the actual `apply_sensitivity_filter` semantics (items whose
  sensitivity meets or exceeds the floor are dropped or redacted, per
  `src/contextweaver/context/sensitivity.py:1-15,118,145-157`). (#338)

## [0.11.1] - 2026-05-28

### Added

- **Community & interop docs** — `CODE_OF_CONDUCT.md` (Contributor Covenant
  2.1) for the GitHub community-standards check (#249); a contributor landing
  page `docs/contributing_paths.md` mapping time-boxed contribution paths to
  files, commands, and labels (#325); a cookbook recipe documenting a
  post-generation safety gate for agent-generated diffs, with no runtime
  dependency (#332); and `docs/interop_skill_cards.md` mapping a reviewed skill
  card onto a `ContextItem` with matching / non-matching examples (#333).
- **Adopter positioning docs** — new adopter-facing benchmark report,
  ecosystem comparison map, stability / Beta / 1.0 readiness checklist, and
  launch kit for reusable public copy and asset links. Issues #323, #324,
  #327, #328, #329.

### Changed

- **README / docs / PyPI positioning** — sharpened the first-screen category
  around "context firewall + tool router for MCP and tool-heavy agents", added
  quick use-fit framing, and aligned package/site descriptions with the same
  wording. Issue #321.

## [0.11.0] - 2026-05-27

### Added

- **Memory-source adapter interface — `context/memory_types.py`,
  `context/memory_fixture.py`, `context/memory_source.py`** (#293).
  New `MemorySource` Protocol in `protocols.py` plus a stdlib-only
  `JsonFixtureMemorySource`, `MemoryEntry` dataclass with `to_dict` /
  `from_dict`, and `memory_entries_to_context_items` /
  `select_memory_for_phase` helpers.  Memory entries materialise into the
  event log as `ContextItem` of kind `memory_fact` and then flow through
  the existing phase filter → sensitivity → firewall → scoring → dedup →
  budget pipeline with no invariant changes.  Phase selection is
  position-graded by scope (`route` prefers `routing` > `tool_preference` >
  `policy`; `call` prefers `tool_usage` > `tool_preference` > `domain`;
  `interpret` prefers `domain` > `fact` > `convention`).  Budgeting charges
  every selected entry at least one token, so short memories cannot bypass
  the cap.  Sensitive entries (≥ active floor) are dropped or redacted by the
  existing `apply_sensitivity_filter` — no new redaction path.  Reserved
  `metadata['_contextweaver']['memory_source']` provenance namespace per
  `docs/agent-context/invariants.md`.
- **Session handoff context pack — `context/handoff_types.py`,
  `context/handoff.py`** (#294).  New `SessionHandoffPack` + `HandoffEntry`
  dataclasses with `to_dict` / `from_dict`, the
  `build_session_handoff_pack(...)` builder, and a `render_handoff_pack(...)`
  deterministic-Markdown renderer.  The pack
  classifies event-log items into five canonical buckets — decisions,
  conventions, unresolved tasks, pitfalls, next inspections — driven by
  explicit `metadata['handoff_category']` tags with a kind-based heuristic
  fallback (`plan_state` → decision, `policy` → convention,
  `tool_result` with `status=failed` → pitfall).  Sensitivity enforcement
  runs *before* classification using the active `ContextPolicy`, then the
  existing context firewall processes surviving `tool_result` items before
  rendering, so the pack cannot leak `restricted` / `confidential` content
  or raw tool-result bodies.  Dependency-closure preserved: every included
  entry's `parent_id` chain is walked to collect deduplicated `ArtifactRef`
  citations.  Pack carries a `version` field (`HANDOFF_PACK_VERSION = "1"`)
  for downstream drift detection.
- **README "When not to use contextweaver" section** (#290). New top-level
  section after `## How contextweaver Solves It` covering the five
  honest non-fits: small tool catalogs (≤ 5 tools), single-shot Q&A
  agents, tiny tool outputs (firewall correctly no-ops), small token
  bills, and non-deterministic LLM-driven routing. Closes the last
  outstanding acceptance criterion of #290; the README opening,
  before/after example, and labelled benchmark claims landed earlier
  with the v0.9 launch-readiness pass.
- **MCP-client integration recipes** (#278, #279). New `docs/recipes/`
  section ships step-by-step guides for putting the contextweaver gateway
  in front of Claude Desktop (`docs/recipes/claude_desktop.md`) and VS
  Code's GitHub Copilot Chat agent mode
  (`docs/recipes/github_copilot.md`). Each recipe links to a
  copy-pasteable client config under `examples/recipes/` and a
  minimal stdio launcher (`examples/recipes/serve_gateway.py`) that
  wires `McpGatewayServer.run_stdio()` against the committed real-catalog
  snapshots under `examples/architectures/mcp_context_gateway/real_catalogs/`
  (`time.json`, `filesystem.json`, `everything.json`). The launcher
  raises `RuntimeError` (not `SystemExit`) on malformed snapshots so
  `main()` honours its documented `int` return contract; argparse-level
  errors continue to exit via `SystemExit` as usual.
  `tests/test_recipes_serve_gateway.py` pins the loader (happy path +
  malformed JSON + missing key + non-list + array payload), the runtime
  builders, the CLI parser, and every `main()` exit-code path
  (clean / keyboard-interrupt / transport-error / malformed-snapshot)
  via a stubbed `asyncio.run`.
- **Pydantic AI adapter — `adapters/pydantic_ai.py`** (#272, child of #193).
  Thin stateless converter turning Pydantic AI `Tool` definitions (live
  instances or the equivalent plain-dict shape `Tool.model_dump()` emits)
  into `SelectableItem`s, plus a lossless `from_pydantic_ai_messages` /
  `to_pydantic_ai_messages` round-trip for `ModelMessage` history. Heavy
  decode/encode helpers live in `adapters/_pydantic_ai_messages.py` to
  keep `pydantic_ai.py` close to the 300-line module guideline. New
  `[pydantic-ai]` optional-dependency group; plain-dict / message-dict
  paths work without the extra installed. New
  `docs/integration_pydantic_ai.md` integration guide,
  `examples/pydantic_ai_adapter_demo.py` (wired into `make example`),
  and 26 new test cases in `tests/test_adapters_pydantic_ai.py`.
- **smolagents adapter — `adapters/smolagents.py`** (#274, child of #193).
  Thin stateless converter turning Hugging Face smolagents `Tool`
  definitions into `SelectableItem`s (with `inputs` → JSON-Schema
  coercion) and a `from_smolagents_agent` step-log ingestor that pulls
  `MultiStepAgent.memory.steps` into `ContextItem`s. `CodeAgent` code
  blocks are intentionally not surfaced — only the executed tool calls
  and their observations land in the event log. New `[smolagents]`
  optional-dependency group; plain-dict / step-dict paths work without
  the extra installed. New `docs/integration_smolagents.md`,
  `examples/smolagents_adapter_demo.py`, and 27 new test cases in
  `tests/test_adapters_smolagents.py`.
- **Agno adapter — `adapters/agno.py`** (#275, child of #193). Thin
  stateless converter turning Agno (formerly Phidata) `Function` and
  `Toolkit` members into `SelectableItem`s, plus a `from_agno_session`
  ingestor that walks an `AgentSession` (or `AgentRun.messages`) into
  `ContextItem`s following the OpenAI Chat Completions message shape
  Agno emits. New `[agno]` optional-dependency group; plain-dict /
  message-dict paths work without the extra installed. The integration
  guide explicitly addresses the contextweaver-vs-Agno-`Memory`
  layering so users understand which layer owns what. New
  `docs/integration_agno.md`, `examples/agno_adapter_demo.py`, and 29
  new test cases in `tests/test_adapters_agno.py`.
- README "Framework Integrations" table (both occurrences) and Examples
  table gained rows for CrewAI, Pydantic AI, smolagents, and Agno.
  `docs/interop.md` matrix promotes Pydantic AI / smolagents / Agno
  from "Planned (#193)" to "Available". `mkdocs.yml` nav surfaces the
  three new integration guides. The umbrella issue #193 closes with
  this release.

### Changed

- **`pyproject.toml` description + keywords** (#248). Project description
  changed from `"Dynamic context management for tool-using AI agents"` to
  `"Context firewall and tool router for tool-heavy AI agents."` to match
  the launch positioning and the README tagline. Three new keywords
  added: `context-firewall`, `tool-router`, `mcp-gateway`. PyPI search
  and the GitHub social card render the description field directly.

### Removed

- **Stale `### 6. Roadmap & Community` block in README** (#242). The
  legacy roadmap text (v0.2 🚧 In Progress — Q2 2026, v0.3 📋 Planned —
  Q3 2026, v1.0 📋 Planned — Q4 2026) was a duplicate of and contradicted
  the accurate `## Roadmap` table further down the README, which was
  refreshed in the v0.9 launch-readiness pass (#252). Removing the stale
  block; the `### Comparison` subsection is renumbered to `### 6.
  Comparison` to keep the "Why Trust contextweaver?" parent section
  numbering contiguous. The community links it previously hosted survive
  via the Discussions badge at the top of the README and the `## License`
  / `[CHANGELOG.md]` reference at the bottom.

### Fixed

- **README / Quickstart onboarding docs refresh** — fixes the README CLI
  `route` example to include required `--catalog`, updates stale version and
  extras tables for the 0.10.x line, documents default sensitivity drops in
  the Quickstart, and explains the offline `tiktoken` fallback /
  `TIKTOKEN_CACHE_DIR` workflow. Issues #307, #309, #310, #311, #312.

## [0.10.0] - 2026-05-22

### Added

- **`contextweaver.routing.hydration`** — public schema-hydration helpers
  (`SchemaSource`, `hydrate_with_schema`, `lazy_schema_resolver`). Reference
  architectures and gateway runtimes can resolve a tool's full input schema
  from a sidecar source (raw dict, JSON file, MCP `tools/list` snapshot)
  without hand-rolling a `_FULL_SCHEMAS` dict. Inline `args_schema` on the
  catalog item still wins; sidecar only fills in when the entry is empty.
  Issue #261.
- **`contextweaver mcp serve` CLI** — new `_mcp_cli.py` Typer sub-app
  (`contextweaver mcp serve`) boots `McpGatewayServer` or `McpProxyServer`
  over stdio against any JSON / YAML catalog. Flags: `--mode {gateway,proxy}`,
  `--gateway` / `--proxy` shortcuts, `--top-k`, `--beam-width`,
  `--cache-stable`, `--name`, `--version`, `--dry-run` (catalog validation
  without binding stdio). Loader accepts both native contextweaver and raw MCP
  `tools/list` snapshot shapes. Marked `[experimental]` for v0.10.
  Issues #243 / #246.
- **Live-transport MCP gateway architecture variant** —
  `examples/architectures/mcp_context_gateway/main_live.py` runs the reference
  architecture through a real `mcp.server.Server` + `ClientSession` paired via
  `mcp.shared.memory` (in-process, deterministic, network-free). Issue #260.
- **Multi-turn MCP gateway architecture variant** —
  `examples/architectures/mcp_context_gateway/main_multi.py` extends the
  scenario to 4 turns (BigQuery → Linear → Slack → PagerDuty) with fact
  accumulation across turns; the turn-1 artifact survives into the final answer
  prompt via dependency closure. Issue #262.
- **`contextweaver demo --scenario mcp-gateway-full`** — surfaces the 60-tool
  reference architecture from the CLI so users can see the full launch
  narrative without invoking the example script directly. The catalog ships
  inside the wheel at `contextweaver/data/mcp_gateway_catalog.yaml`, exposed
  via `contextweaver.data.gateway_catalog_path()`. Issue #264.
- **Gateway-scenario benchmark suite** — `benchmarks/gateway_benchmark.py`
  runs 5 deterministic gateway-shaped scenarios over the same 60-tool catalog
  and emits `benchmarks/results/gateway_latest.json` +
  `benchmarks/gateway_scorecard.md`. Headline firewall-reduction range:
  **0.0 % – 98.8 %** across scenarios. `make benchmark-gateway` /
  `make gateway-scorecard{,-check}` targets added. Issue #270.
- **Real-MCP catalog architecture variant** —
  `examples/architectures/mcp_context_gateway/main_real.py` runs the same
  shape against committed snapshots of three real MCP servers
  (`server-time`, `server-filesystem`, `server-everything`) under
  `real_catalogs/`. `scripts/capture_mcp_catalog.py` is the offline-safe
  regenerator. Issue #280.
- **Asciinema recordings for the showcase demos** — `scripts/record_demo.py`
  is a stdlib-only writer for the asciinema v2 cast format. Four committed
  casts under `docs/assets/casts/` (default, large-catalog, huge-tool-output,
  mcp-gateway-full) linked from `docs/showcase.md`. `make record-demos{,-check}`
  targets added. Issue #281.
- **`RouteResult.to_dict` / `from_dict`** — adds the missing serialization pair
  to `RouteResult`. Default `include_items=True` embeds full `SelectableItem`
  dicts; opt-in `include_items=False` emits the cheaper ID-only payload.
  Issue #289.
- **Context-pack explanation traces** — new `contextweaver.context.explanation`
  module exposes `ContextBuildExplanation` + `CandidateExplanation` versioned
  dataclasses capturing per-candidate scoring, drop reasons,
  dependency-closure additions, and sensitivity / dedup drops. Surface is
  opt-in: `ContextManager.build(..., explain=True)` returns a `(pack,
  explanation)` tuple; the default `explain=False` return type is unchanged.
  Issue #291.
- **Sensitivity / firewall regression fixtures** — six explicit fixtures under
  `tests/fixtures/sensitivity/` (public, internal, confidential, restricted,
  PII-like, secret-like) driven through `apply_sensitivity_filter` at every
  floor level in both drop and redact modes. Pins the conservative default
  (`confidential` floor + `drop`) and the `MaskRedactionHook` invariant.
  Issue #292.
- **Weaver-spec payload fixtures** — checked-in `tests/fixtures/weaver_spec/`
  fixtures driven through the `adapters.weaver_contracts` adapter. The CI
  `weaver-spec conformance` step gained a `--fixtures-dir` flag that cites the
  exact failing fixture path, JSON pointer, and schema on failure. Issue #295.
- **Golden route-prompt + MCP-ingestion fixtures** — `tests/fixtures/golden/`
  snapshots `ContextManager.build_route_prompt_sync` and
  `mcp_result_to_envelope` outputs. Shared `tests/fixtures/_normalize.py`
  strips volatile fields (timestamps, UUIDs, score floats > 4 dp) for
  machine-stable comparisons. Issue #296.

### Fixed

- **MCP server call-tool result shape** — `McpGatewayServer` and
  `McpProxyServer` now return fully-built `CallToolResult` objects instead of
  `(content, is_error)` 2-tuples. Newer MCP SDK versions interpret a 2-tuple
  as `(unstructured, structuredContent)` and reject the `bool` half via
  JSON-schema validation, breaking the live-transport path. Surfaced while
  landing #260.
- **`SchemaSource.from_json_file` validation** — tightened schema-key checks
  and narrowed the example error catch to `json.JSONDecodeError` (was bare
  `Exception`). Issue #261 follow-up.
- **`CallToolResult.content` type annotation** — narrowed from `Any` to
  `list[ContentBlock]` for `mypy --strict` compliance. Issue #300.
- **`ContextBuildExplanation` overload** — added `assert explanation is not
  None` guard when `explain=True` so both `@overload` branches satisfy mypy.
  Issue #291 follow-up.
- **`PYTHONPATH` in pytest config** — added `pythonpath = ["src", "tests"]` to
  `pyproject.toml` so `tests.fixtures` package imports resolve correctly in
  all invocation styles. Issue #302.

## [0.9.1] - 2026-05-21

### Added

- **Benchmark scorecard transparency suite** (#266 #267 #268 #269 #271 #277).
  Seven scorecard expansions in a single cluster:
  - `HashingEmbeddingBackend` — stdlib-only deterministic `EmbeddingBackend`
    (blake2b hashing trick, L2-normalised vectors) so the embedding code path
    runs in CI without pulling torch. Re-exported from
    `contextweaver.extras.embeddings`. Issue #266.
  - Hardware reference-rig disclosure — benchmark harness captures `platform` /
    `sys` / `os.cpu_count` metadata; scorecard renderer emits the pinned
    canonical rig and the measured-on host as separate blocks. Issue #267.
  - Tiktoken parity check — `_run_tiktoken_parity` quantifies
    `CharDivFourEstimator` vs `cl100k_base` drift (MAE, max, signed, ratio).
    Issue #268.
  - Optional end-to-end real-model probe — `--with-real-model` flag plus
    `CW_BENCH_LLM_PROVIDER` / `CW_BENCH_LLM_API_KEY` env vars; off by default,
    CI never invokes the network path. New `[e2e-eval]` extra. Issue #269.
  - Small-payload scenarios — `benchmarks/scenarios/tiny_payload.jsonl` and
    `mixed_payload.jsonl` make the firewall `compaction == 1.00×` no-op
    behaviour explicit. Issue #271.
  - Head-heavy + long-tail mixed-namespace catalog — new `--mixed-shapes`
    matrix block at `catalog_size=500` against an asymmetric namespace
    distribution. Issue #277.
  - `benchmark_version` bumped to `1.2`; JSON output is purely additive.

## [0.9.0] - 2026-05-20

### Added

- **`ProxyRuntime(cache_stable=True)`** — new opt-in parameter that inserts a
  cache-breakpoint marker between previously-seen and newly-routed choice cards,
  enabling LLM prompt-cache hits across successive browse calls. Issue #283.
- **CLI budget regression checks** (#276). New `contextweaver budget-check`
  command rebuilds an ingested session for a selected phase, compares the
  rendered prompt token count against `--max-tokens`, exits 1 on budget
  overruns, and supports `--breakdown`, `--json`, and `--ratchet` baseline
  workflows for CI.

## [0.8.0] - 2026-05-19

### Added

- **CrewAI adapter — `adapters/crewai.py`** (#193, Phase 1).  New
  thin stateless converter that turns CrewAI tool definitions (live
  `crewai.tools.BaseTool` instances or the equivalent plain-dict
  shape returned by `BaseTool.model_dump()`) into
  `SelectableItem`s.  Ships `crewai_tool_to_selectable`,
  `crewai_tools_to_catalog`, `infer_crewai_namespace`, and
  `load_crewai_catalog`.  The dict-conversion path works without
  the `[crewai]` extra installed; `load_crewai_catalog` consumes
  live `BaseTool` instances when the extra is available.  New
  `[crewai]` optional-dependency group + `crewai>=0.80` added to
  `[dev]` so CI exercises the real upstream wire shape.  New
  `docs/integration_crewai.md` integration guide and
  `examples/crewai_adapter_demo.py` (wired into `make example`).
  Follow-ups for Pydantic AI / smolagents / Agno tracked on the
  same issue.
- **Mem0 external-memory backend — `extras/memory/mem0.py`** (#195,
  Phase 1).  `Mem0EpisodicStore` + `Mem0FactStore` implement the
  existing `store.protocols.EpisodicStore` / `FactStore` Protocols
  verbatim (no Protocol widening — see
  `docs/agent-context/invariants.md`).  Writes go through
  `mem0.Memory.add(infer=False)` so the raw text is stored as-is;
  every record is stamped with `cw_episode_id` / `cw_fact_id` in
  its metadata so canonical-ID resolution survives mem0's UUID
  generation.  New `[mem0]` optional-dependency group + `mem0ai>=0.1`
  added to `[dev]`.  New `docs/integration_memory.md` decision
  matrix covering Mem0 / Zep / LangMem (the latter two follow-ups
  against the same Protocol shape).  Documented in
  `docs/cookbook.md` §6 and `docs/interop.md` interop matrix.

### Changed

- **Provider-SDK leak invariant tests run in a subprocess.**
  `test_module_does_not_import_provider_sdk_at_load_time` in
  `tests/test_adapters_openai_messages.py`,
  `tests/test_adapters_anthropic_messages.py`, and
  `tests/test_adapters_gemini_contents.py` previously asserted
  the absence of `openai` / `anthropic` / `google.generativeai`
  from `sys.modules` in the running session — which made the
  assertion sensitive to whatever any other test (in our case
  `tests/test_adapters_crewai.py`'s live `BaseTool` test, since
  `crewai` transitively pulls `openai`) had already imported.
  The tests now spawn a fresh interpreter, import only the
  contextweaver adapter under test, and assert against that
  process's `sys.modules`.  The invariant they check is now
  independent of test ordering and other installed extras.

## [0.7.0] - 2026-05-18

### Added

- **Explicit routing pipeline** (#56). The monolithic `Router.route()` is
  refactored into four named, swappable stages: *retrieve* → *rerank* →
  *navigate* → *pack*.  New `RoutingPipeline` composer
  (`routing/pipeline.py`) plus `Navigator` (`routing/navigator.py`) and
  `CardPacker` (`routing/packer.py`) protocols + bundled defaults
  (`BeamSearchNavigator`, `DefaultCardPacker`).  `Router` continues to
  expose its full public API unchanged and now delegates internally via
  the new `Router.pipeline` property.  Default pipeline output is
  byte-identical to the pre-refactor implementation (verified by the
  existing 50+ `tests/test_router.py` regression gate and
  `make scorecard-check`).
- **Optional embedding-based retrieval backend** (#8). New
  `EmbeddingBackend` protocol in `protocols.py`; new
  `[embeddings]` extra (`pip install 'contextweaver[embeddings]'`)
  wires `SentenceTransformerBackend` and `HybridEmbeddingRetriever` in
  `contextweaver/extras/embeddings.py`.  `Router(embedding_backend=...)`
  combines the embedding signal with TF-IDF (70/30 weighted sum by
  default) so exact-id / exact-tag lexical hits keep their floor.
  Zero-dependency default path is unchanged.
- **History-aware re-routing** (#27 Phase 1).  `Router.route(...,
  history=RouteHistory(...))` deprioritises already-called tools
  (repeat-penalty multiplier), boosts candidates whose `description`
  resembles the most recent tool-result summary, and surfaces per-item
  score deltas on the new `RouteResult.history_adjustments` field.
  `ContextManager.build_route_prompt` auto-constructs the history from
  the event log unless `history_from_log=False` is set.
- **Tool-dependency metadata on `SelectableItem`** (#27 Phase 2).  New
  optional `depends_on` / `provides` / `requires` fields drive a
  dependency-satisfaction boost and an unsatisfied-`depends_on`
  penalty in history-aware routing.  All three default to `None` and
  are omitted from `to_dict()` when unset, so existing catalogs
  round-trip unchanged.  `Catalog.validate_dependencies()` returns
  human-readable warnings for `depends_on` references to unknown
  tool ids.  `schemas/catalog.schema.json` regenerated.
- **FastMCP CodeMode hooks** (#87). New
  `contextweaver.adapters.fastmcp.make_discovery_tool(router, catalog)`
  and `make_context_hook(context_manager)` factories return plain
  callables suitable for FastMCP CodeMode's custom-discovery-tool and
  context hooks (or any runtime with the same shape — LangChain,
  LlamaIndex, hand-rolled agent loops). Neither hook imports `fastmcp`
  at runtime; the callable contract is framework-agnostic.
  `examples/fastmcp_discovery_demo.py` demonstrates a 22-tool catalog
  shrinking to a 3-tool shortlist (86% token reduction). `fastmcp>=2.0`
  is now part of the `[dev]` extra so a real in-memory FastMCP server
  integration test (`tests/test_adapters_fastmcp_discovery.py`) runs on
  every CI matrix cell. Reference:
  https://github.com/PrefectHQ/fastmcp/discussions/3365
- **Code-review bot reference architecture** (#204). New
  `examples/architectures/code_review_bot/` walks a six-step pull-request
  review against a 24-tool catalog (grep / git / lint / typecheck /
  test / review). The firewall is the load-bearing pattern: a synthetic
  ~28 KB diff dump and ~2.5 KB grep result both compact to ~500-char
  summaries while raw bytes stay addressable via the artifact store.
  Linked from `docs/architectures/index.md` and runnable under
  `make architectures`.
- **Voice agent reference architecture** (#205). New
  `examples/architectures/voice_agent/` is the canonical worked example
  for `docs/integration_pipecat.md`. Walks a five-turn customer-service
  call against an 18-tool catalog, demonstrating the
  `asyncio.to_thread(mgr.build_sync, …)` pattern and tight per-phase
  budgets (`ContextBudget(route=200, call=500, interpret=400,
  answer=1000)`) for sub-300 ms TTS. Pipecat is optional via the new
  `[voice]` extra; the example runs end-to-end without it.

### Fixed

- **FastMCP CodeMode hook factories use `ConfigError`** (PR #233 review).
  `make_discovery_tool` and `make_context_hook` now raise
  `contextweaver.exceptions.ConfigError` (a `ContextWeaverError` subclass)
  on negative `top_k` / `firewall_threshold`, replacing the previous bare
  `ValueError` per the AGENTS.md custom-exception convention.
- **`make_discovery_tool` `top_k` counts hydratable tools, not slots**
  (PR #233 review). The discovery hook used to slice
  `result.candidate_ids` to `top_k` *before* hydration, so a graph-only
  candidate appearing early in the list silently shrank the shortlist by
  one. The hook now iterates the full candidate list, skips non-hydratable
  IDs, and stops after `top_k` real tools have been appended.
- **`make_discovery_tool` returns deep-copied `input_schema`** (PR #233
  review). `dict(hydrated.args_schema)` was a shallow copy that left
  nested dicts / lists aliased with the catalog item, so an external
  runtime mutating the returned schema could silently corrupt subsequent
  `discover()` calls. The schema is now `copy.deepcopy`-ed before
  handing it to callers.
- **`make_context_hook` docstring matches implementation** (PR #233
  review). The "parent user-turn id for dependency closure" wording is
  replaced with the actual behaviour — the query is stamped onto
  `item.metadata["codemode_query"]`, no synthetic user_turn item is
  ingested, matching the inline rationale that the hook is intentionally
  stateless w.r.t. conversation history.
- **`make_context_hook` accepts `tool_name`** (PR #233 audit). The
  factory previously hardcoded `tool_name="codemode.discovery"` on every
  firewalled `ContextItem`, which was both semantically wrong (this is a
  context / firewall hook, not a discovery hook) and lossy for multi-tool
  agents whose traces could no longer be sliced by underlying tool. The
  factory now accepts `tool_name: str = "codemode.tool_result"` and stamps
  the configured value into the event log. The docstring also pins the
  timing of the `codemode_query` metadata stamp (post-firewall;
  visible to event-log reads and `on_context_built` callbacks but
  *not* to `on_firewall_triggered`).
- **FastMCP CodeMode test coverage** (PR #233 audit). Added a real
  FastMCP integration test that exercises `make_context_hook` end-to-end
  against an in-memory `fastmcp.FastMCP` server (`tests/test_adapters_
  fastmcp_discovery.py::test_context_hook_compacts_real_fastmcp_tool_call`),
  a `top_k=0` boundary test for `make_discovery_tool`, and a post-hook
  metadata-consumability pin for the `codemode_query` contract.

## [0.6.0] - 2026-05-17

### Fixed

- **`SqliteEventLog.query()` filter order matches `InMemoryEventLog`**
  (PR #232 review). `since` is now applied to the full insertion-ordered
  log *before* the `kinds` filter, mirroring the in-memory semantics.
  Previously the SQL path filtered by kind first, which gave different
  results on mixed-kind logs for the same `(kinds, since, limit)` triple.
- **`JsonFileArtifactStore` path-traversal hardening** (PR #232 review).
  Handle validation moved into `_meta_path` / `_data_path` so every
  public method that resolves a handle (`get` / `ref` / `exists` /
  `delete` / `metadata` / `drilldown`) rejects path separators, `..`,
  `.`, and null bytes — not just `put`.
- **`SqliteEventLog` use-after-close raises `StoreClosedError`** (PR
  #232 review). The bare `RuntimeError` previously raised by
  `_require_conn` is replaced by a new
  `contextweaver.exceptions.StoreClosedError` (subclass of
  `ContextWeaverError`) so callers can catch the contextweaver-family
  consistently per `AGENTS.md`.
- **`JsonFileArtifactStore.list_refs()` skips wrong-shape JSON** (PR
  #232 review). The error-handling clause now also catches `TypeError`
  raised by `ArtifactRef.from_dict` when a `.json` file is valid JSON
  but the top level is not a mapping (e.g. `[]`, `null`, a bare string).

### Added

- **`SqliteEventLog` + shared `_sqlite_base.py`** (#174, #223). First
  persistent `EventLog` backend, layered on a small connection +
  migration helper that the rest of the SQLite-stores epic will reuse.
  Sets `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON` on open,
  versions schema migrations through a `_contextweaver_schema_version`
  table, and round-trips every `ContextItem` field (including JSON
  `metadata` and nested `ArtifactRef`). Constructor accepts a filesystem
  path or `":memory:"`; the parent directory is created automatically.
  Single-process; sync only. New `[sqlite]` extras-group placeholder.
- **`JsonFileArtifactStore`** (#42). Filesystem-backed
  `ArtifactStore` implementation that stores each artifact as a
  `{base_dir}/{handle}.data` byte file plus a `{base_dir}/{handle}.json`
  metadata file. Re-instantiating against the same directory recovers
  the metadata index automatically. Handles containing path separators,
  `..`, `.`, or null bytes are rejected at write time. Drilldown
  selectors (`head` / `lines` / `json_keys` / `rows`) match
  `InMemoryArtifactStore` byte-for-byte via a shared module-private
  helper `_apply_selector` in `store/artifacts.py`.
- **`EventLog` lifecycle methods** (#223). The `EventLog` protocol now
  requires `close()`, `__enter__`, and `__exit__` so persistent backends
  fit the contract cleanly. `InMemoryEventLog.close()` is a no-op so
  existing callers are unaffected; the methods make
  `with SqliteEventLog(path) as log:` the recommended idiom for the new
  backend.

- **`BuildStats.report()` and `BuildStats.report_dict()`** (#106). New
  diagnostic-report surface on `BuildStats`: pure-data string rendering
  (`"text"` or `"rich"` Rich-markup format) plus a versioned dict for
  programmatic consumers. Includes phase, budget, candidate counts,
  per-section token breakdown, drop reasons, and budget-utilisation
  recommendations. Output is deterministic (sorted keys, stable
  spacing).
- **`BuildStats.prompt_tokens` property** (#106). Single source of
  truth for `sum(tokens_per_section.values()) + header_footer_tokens`
  — replaces six inline computations across `extras/otel.py`,
  `__main__.py`, `metrics.py`, and example scripts.
- **`contextweaver stats` CLI subcommand** (#106). Renders the
  `BuildStats` report from an ingested session JSON. Supports
  `--phase` / `--budget` / `--format {rich,text}`.
- **`RouteResult.explanation()`** (#226). New pure-data method on
  `RouteResult` that renders a paste-friendly Markdown rationale of
  the routing decision — top-k table, confidence gap, ambiguity flag,
  applied context hints, excluded/gated filter counts. `format="dict"`
  returns a versioned (`{"version": 1, ...}`) structured payload for
  programmatic consumers. Logic lives in the new
  `src/contextweaver/routing/explanation.py` module to keep `router.py`
  under the soft 300-line cap. `docs/troubleshooting.md` gains a
  paste-ready example.
- **JSON Schemas + drift gate** (#225, closes #196). Six committed
  schemas under `schemas/` and `docs/schemas/v0/`:
  `catalog.schema.json`, `choice_card.schema.json`,
  `result_envelope.schema.json`, `route_trace.schema.json`,
  `build_stats.schema.json`, `graph_manifest.schema.json`. Stable `$id`
  URLs (`https://dgenio.github.io/contextweaver/schemas/v0/...`).
  Generator (`src/contextweaver/_schema_gen.py`, stdlib-only) is
  deterministic; `make schemas` regenerates; `make schemas-check`
  fails on drift and is wired into `make ci` and
  `.github/workflows/ci.yml`. `ChoiceCard` size bounds from
  `docs/gateway_spec.md` §2 round-trip into the schema as `maxLength`
  / `maxItems` and are also enforced at construction time via
  `__post_init__`. `examples/sample_catalog.yaml` gains a
  `# yaml-language-server: $schema=...` header. New
  `docs/contracts.md`.
- **OpenTelemetry GenAI semantic conventions** (#224). Rewrite of
  `extras/otel.py` to emit `invoke_agent`-shaped spans for
  `ContextManager.build()` and `execute_tool`-shaped spans for
  `Router.route()`, populating the stable subset of `gen_ai.*`
  attributes (`gen_ai.system="contextweaver"`,
  `gen_ai.operation.name`, `gen_ai.usage.input_tokens`,
  `gen_ai.tool.name`) plus an engine-specific `contextweaver.*`
  namespace for routing-candidate detail. Token-usage histogram now
  uses the canonical `gen_ai.client.token.usage` metric name. New
  `docs/integration_otel.md` with Laminar + Phoenix worked examples
  and PII-safety guidance. Default emission is PII-safe; experimental
  attributes gated behind `otel_emit_experimental=False`. Tests use
  `InMemorySpanExporter` for deterministic SemConv-name assertions.
- **One-shot provider-message ingestion adapters** (#194, #219, #222). Three
  new sibling modules under `src/contextweaver/adapters/` —
  `openai_messages.py`, `anthropic_messages.py`, `gemini_contents.py` — each
  shipping a `from_*` decoder (plain provider dicts → `ContextItem`s with
  `parent_id` chains, optional `into=ContextManager` for direct ingestion)
  and a `to_*` inverse for round-tripping back into the provider SDK. Users
  with existing OpenAI / Anthropic / Gemini agents can now drop contextweaver
  in with 5 lines (excluding imports). No provider SDKs are imported at
  module load time; adapters operate on plain `dict`s per the `adapters/`
  path convention. Round-trip equality is enforced by parametrised fixture
  tests for every provider (`tests/test_adapters_*_messages.py`,
  `tests/test_adapters_gemini_contents.py`).
- **Quickstart "Adopting from an existing chat history" section** (#220).
  `docs/quickstart.md` now leads with a 5-line drop-in snippet for adopters
  arriving with an existing OpenAI / Anthropic / Gemini session. `README.md`
  Quickstart and `docs/which_pattern.md` cross-link to it.
- **`make_choice_cards` byte-stable ordering contract** (#218). The existing
  `(-score, +id)` ordering is now an explicit guarantee in the docstring and
  is locked by a regression test
  (`test_make_choice_cards_byte_identical_stable_order`) that asserts
  byte-identical JSON across two consecutive calls on the same input. New
  "Prompt-caching compatibility" section in `docs/integration_mcp.md`
  documents how to leverage this for Anthropic `cache_control` prefixes
  (and the OpenAI / Google equivalents).
- **"Context engineering" positioning in docs landing** (#217). The
  `docs/index.md` tagline and a new "Why context engineering matters"
  callout in `docs/architecture.md` establish the discipline framing
  alongside the existing README headline (already updated in 0.4.0) and the
  `context-engineering` keyword in `pyproject.toml`.

### Changed

- **CLI: argparse → Typer + Rich** (#221).
  `src/contextweaver/__main__.py` rewritten on top of
  [Typer](https://typer.tiangolo.com) with Rich-formatted help,
  panels, and tables. All seven existing subcommands (`demo`,
  `build`, `route`, `print-tree`, `init`, `ingest`, `replay`) keep
  their flag names — `tests/test_cli.py` still exercises every one.
  New `stats` subcommand from #106 is wired on the same host.
  Running `python -m contextweaver` without a subcommand now exits
  with code 2 (Typer/Click convention) instead of 0; the no-args
  smoke test was updated to accept either.
- **Core dependencies**: `typer>=0.9` and `rich>=13.0` move from the
  `[cli]` extra into core. The `[cli]` extra is kept as an empty alias
  for one cycle (scheduled for removal in v0.6). The `_HAS_RICH`
  guarded-import dance in `__main__.py` is gone.
- **`ChoiceCard.kind`** tightened from `str` to
  `Literal["tool", "agent", "skill", "internal"]` so the published
  `choice_card.schema.json` carries the kind enumeration directly.
  Construction-time validation (`__post_init__`) enforces the
  gateway-spec §2 size bounds (`name` ≤ 64 chars, ≤ 5 tags each
  ≤ 24 chars) on every path including `ChoiceCard.from_dict`.
- **`opentelemetry-api`/`-sdk` floor bumped** from `>=1.20` to `>=1.27`
  in the `[otel]` extra so the
  `opentelemetry.semconv._incubating.attributes.gen_ai_attributes`
  module is available; `opentelemetry-semantic-conventions>=0.48b0`
  added as a direct extra dep.

### Removed

- **Old OTel span names**: `contextweaver.context.build` and
  `contextweaver.routing.route` no longer emit. Dashboards keyed on
  those names need to be re-keyed to `invoke_agent` and
  `execute_tool`. The engine-specific
  `contextweaver.context.firewall` and `contextweaver.context.exclude`
  spans are preserved (no SemConv equivalent yet).
- **Old OTel metric name**: `contextweaver.tokens.used` histogram
  renamed to the canonical `gen_ai.client.token.usage`. The
  engine-specific counter / histogram names
  (`contextweaver.firewall.interceptions`,
  `contextweaver.items.excluded`, `contextweaver.budget.exceeded`,
  `contextweaver.routing.candidates`) are preserved.
- **`OTelEventHook` attribute-key prefix**: engine-specific span
  attributes moved from bare names (`phase`, `tokens`, `reason`) to
  the `contextweaver.*` namespace to avoid colliding with future
  SemConv additions.

### Fixed

- **`--backends` typo validation in benchmark harness** (PR #235). Typos
  like `--backends tifdf` now exit via `parser.error()` (code 2) instead of
  propagating to `Router` init as a `ConfigError` traceback.
- **Skipped-row rendering in benchmark delta script** (PR #235). Matrix
  cells with `status != "ok"` (e.g. `"skipped: rapidfuzz not installed"`)
  now render as `_skipped_ (reason)` instead of being treated as a
  zero-metric regression with false-positive ⚠️ markers.

## [0.4.0] - 2026-05-16

### Added

- **Namespace-aware tokenizer** (#213). `_utils.tokenize` is now the single
  source of truth for splitting dotted / hyphenated / slashed tool ids
  (`crm.deals.search` → `{"crm.deals.search", "crm", "deals", "search"}`)
  plus colon-separated alternates (`admin:users:create`). Underscored
  compounds are intentionally kept as single tokens — empirical measurement
  on the v0.3.0 benchmark showed splitting them inflates cross-talk with
  synthetic catalog variants (rationale captured in
  `_OUTER_SPLIT_RE` docstring). Retires the per-call
  `replace(":"/"_"/"/" ) ` workaround in `routing/router.py`.
- **Per-backend × per-size benchmark matrix** (#208). New `--matrix`,
  `--backends`, `--sizes` flags on `benchmarks/benchmark.py`. Emits
  additive `routing_matrix` rows (`tfidf` / `bm25` / `fuzzy` × 100 / 500 /
  1000 by default) without disturbing the legacy `routing` single-backend
  summary. Missing backends record an explicit `status: skipped: missing
  rapidfuzz` row. New `make benchmark-matrix` target.
- **Expanded routing gold set 50 → 200** (#209). `benchmarks/routing_gold.json`
  now carries 200 naturalistic queries (25 per namespace), with an explicit
  `namespace` field on every entry. Drives the new
  `routing_per_namespace` aggregation in `latest.json`. Every `expected`
  id is catalog-validated before commit.
- **Naïve-concat baseline** (#215). New `scripts/baseline_naive.py` (stdlib
  + `tiktoken`) computes a "dump all tool schemas + history" baseline and
  emits an additive `naive_delta` block per `context` row in `latest.json`.
  Coverage proxy is `items_included / event_count` — documented,
  deterministic, no LLM judge required.
- **Sticky benchmark-delta PR comment** (#211). New
  `scripts/benchmark_delta.py` renders a head-vs-base markdown delta with
  shared ✅/⚠️ marker conventions. CI job `benchmark-comment` posts a
  sticky comment (one per PR, updated in place) using `peter-evans/`
  `find-comment` + `create-or-update-comment`. Adds an encouraged
  "Reproducibility" subsection to the PR template.
- **`ScoringConfig` weight sweep** (#214). New `scripts/sweep_scoring.py`
  + `make sweep-scoring`. Grid-searches 243 configurations against the
  committed scenarios, ranks them by a documented composite, and emits
  `benchmarks/sweep_scoring.md`. The current `ScoringConfig` defaults are
  **not** changed by this PR — Pareto-dominating configs (if any) are
  flagged for a deliberately-scoped follow-up.
- **Weekly scorecard regeneration cron** (#207). New
  `.github/workflows/scorecard-weekly.yml` runs `make benchmark-matrix &&
  make scorecard` Monday 06:00 UTC and opens a `chore/weekly-scorecard`
  PR on drift via `peter-evans/create-pull-request`. Independent of the
  main CI; never gates other workflows.
- **`add-eval.prompt.md`** (#216). New agent prompt at
  `.github/prompts/add-eval.prompt.md` matching the structure of the
  existing three prompts. Cross-linked from `AGENTS.md` and
  `docs/agent-context/workflows.md`. Codifies the
  matrix → scorecard → regression-comment workflow for new evals.
- **Scorecard renderer** gains additive matrix, per-namespace, and
  naïve-delta sections (`scripts/render_scorecard.py`). Empty when the
  underlying JSON keys are absent — keeps PR #203's baseline scorecard
  valid until the matrix and naïve numbers are wired in.

### Earlier in the Unreleased cycle

- **Discoverability polish** (#200). Six small README/PyPI metadata changes
  that together make contextweaver easier to find and to evaluate from
  outside the repo: README badge row (CI, PyPI version, Python versions,
  license, docs site, Discussions); "context engineering" as a secondary
  phrase in the intro and Problem sections; `CITATION.cff` at repo root
  (CFF v1.2) for the "Cite this repository" button; `Documentation` and
  `Issues` and `Discussions` URLs in `pyproject.toml` `[project.urls]`;
  new `Framework :: AsyncIO` and `Topic :: Scientific/Engineering ::
  Artificial Intelligence` classifiers; extended keywords
  (`context-engineering`, `mcp`, `tool-routing`, `prompt-budgeting`,
  `agent-infrastructure`). The social-preview PNG is intentionally
  deferred to a follow-up (requires a GitHub UI step beyond a code PR).
- **Decision-tree landing page** (#199). New `docs/which_pattern.md`
  branches on the user's symptom (long conversations, large catalogs,
  huge tool outputs, real-time agents, MCP server, BYO tools, "I want a
  realistic template") and lands each branch on one concrete next step.
  Linked from the README Quickstart section and from the top of
  `docs/architecture.md` and `docs/interop.md`; surfaced as the second
  nav entry in `mkdocs.yml`.
- **Production reference architectures cookbook — Slack ops bot** (#198,
  partial). First reference architecture under
  `examples/architectures/slack_ops_bot/`: 48-tool YAML catalog, six-turn
  scripted incident-response transcript, mocked tool backends, firewall
  on a 34 KB log dump, persistent facts that survive across turns.
  Demonstrates the bounded-choice pattern (Router narrows 48 → 3, the
  bot picks from the shortlist). Runnable under `make architectures` /
  `make example`; documented at `docs/architectures/slack_ops_bot.md`
  with captured output in `examples/architectures/slack_ops_bot/OUTPUT.md`.
  Code-review bot and voice agent architectures are tracked as follow-ups.
- **Benchmark scorecard** (#197). New `make scorecard` target renders
  `benchmarks/scorecard.md` from `benchmarks/results/latest.json`
  deterministically (stdlib-only Python via `scripts/render_scorecard.py`).
  Committed scorecard surfaces top-k recall, latency percentiles, and
  context-pipeline metrics (drops, dedup, compaction) for every
  configuration. New `make scorecard-check` gating CI step prevents
  drift between the committed `latest.json` and the committed scorecard;
  `docs/benchmarks.md` documents the methodology, scope, and follow-up
  work (per-backend matrix, weekly scheduled regeneration). The
  scorecard link is added to the README "Why Trust" section.
- **Stress-test benchmark scenario** (#181). New
  `benchmarks/scenarios/stress_conversation.jsonl` — a SEV2
  incident-response transcript with three large tool results (≥ 2 KB
  raw, firewall fires), four near-duplicate agent messages (dedup
  fires), and total prompt content that pushes the 6000-token answer
  budget past 100% utilization (`items_dropped > 0`). The other three
  scenarios remain as "light load" baselines so reviewers can see the
  difference between unloaded and stressed pipeline behaviour. The
  `benchmarks/results/latest.json` baseline is regenerated; the
  benchmark README's metrics table is refreshed to match.

### Changed

- `make example` now includes the Slack ops bot architecture (via the
  new `make architectures` umbrella target). `make fmt` / `make lint`
  now cover `scripts/` so the renderer stays clean.
- `benchmarks/results/latest.json` is now committed (no longer
  documented as git-ignored) so the scorecard renders against a known
  baseline. `benchmarks/README.md` table and prose updated accordingly.

### Added (from PR #202)

- **Weaver-spec interop** (#143, #145, #151). New
  `RoutingDecision` dataclass in `contextweaver.envelope` mirroring the
  field set of the `weaver_contracts.RoutingDecision` contract (id,
  choice_cards, timestamp, selected_item_id, selected_card_id,
  context_summary, metadata) with `to_dict`/`from_dict` and ISO 8601
  timestamp serialization. Note that `choice_cards` stores a flat list of
  contextweaver 1:1 `ChoiceCard` instances — schema-valid spec JSON requires
  going through `adapters.weaver_contracts.to_weaver_routing_decision()`,
  which groups the cards into a single spec `ChoiceCard` menu. New
  `RouteResult.to_routing_decision(...)` helper builds a spec-aligned decision
  from a routing call (preserving router diagnostics under
  `metadata["contextweaver"]`). New
  `contextweaver.adapters.weaver_contracts` module providing lossless
  `to_weaver_*` / `from_weaver_*` round-trips for `SelectableItem`,
  `ChoiceCard`, `RoutingDecision`, and `Frame` (via `ResultEnvelope`);
  contextweaver-specific fields are preserved under `metadata["_contextweaver"]`.
  New optional extra `contextweaver[weaver-spec]` and `[dev]` dep on
  `weaver_contracts >= 0.2, < 1` + `jsonschema >= 4`. New README section
  declaring `weaver_contracts >= 0.2.0, < 1.0` compatibility, satisfied
  invariants (I-03, I-05), and an exact pointer to the round-trip API. New
  `docs/weaver_spec_mapping.md` documenting the field-by-field mapping and
  the `SelectableItem` / `ChoiceCard` name-clash convention. New
  `scripts/weaver_spec_conformance.py` and `make weaver-conformance` target
  that runs a Python round-trip plus JSON-Schema validation against the
  published schemas at `https://weaver-spec.dev/contracts/v0/`; wired into
  CI as a gating step (the issue allowed a non-gating stub, but the
  available `weaver_contracts` + `jsonschema` dev deps unlock real schema
  validation today).
- **MCP proxy + two-tool gateway runtime** (#13, #28, #29, #34).  Lands the
  full proxy/gateway surface specified by `docs/gateway_spec.md`:
  - `src/contextweaver/routing/tool_id.py` — canonical `tool_id` grammar
    (`parse_tool_id`, `format_tool_id`, `compute_hash8`, `canonical_tool_id`,
    `ToolIdParts`) per §1.  `mcp_tool_to_selectable` is cut over to emit
    canonical ids (§1.7); the legacy `mcp:{name}` form is retired.
  - `src/contextweaver/routing/path.py` — `tool_browse` path-navigation
    grammar (`parse_path`, `resolve_path`) per §3, with two new typed
    exceptions (`PathInvalidError`, `PathNotFoundError`).
  - `src/contextweaver/routing/cards.py` — refit to **token-native**
    enforcement of the §2.3 ChoiceCard size bounds against
    `cl100k_base` (`make_choice_cards`, `bound_browse_response`,
    `truncate_description_to_tokens`, `count_tokens`).  The old
    `max_total_chars` / `max_desc_chars` arguments are removed.
  - `src/contextweaver/adapters/proxy_runtime.py` — `ProxyRuntime`
    shared core with `ExposureMode`, `UpstreamCall` Protocol, and
    browse / execute / view / hydrate / strip_tools_list primitives
    (#29).  Validates `tool_execute` args against the hydrated schema
    via `jsonschema` (§4.4).
  - `src/contextweaver/adapters/mcp_gateway.py` — three meta-tools
    (`tool_browse`, `tool_execute`, `tool_view`) with structured
    `GatewayError` returns (§3.4) (#28, #34).
  - `src/contextweaver/adapters/mcp_proxy.py` — transparent-proxy
    surfaces: stripped `tools/list` + `tool_hydrate` + `tool_execute`
    (§4.1) (#13).
  - `src/contextweaver/adapters/mcp_upstream.py` — concrete
    `UpstreamCall` adapters: `StubUpstream` (in-process tests / demos),
    `McpClientUpstream` (single MCP `ClientSession`), and
    `MultiplexUpstream` (multi-server fan-out).
  - `src/contextweaver/adapters/mcp_gateway_server.py` /
    `mcp_proxy_server.py` — bind the dispatch layers onto a real
    `mcp.server.Server` over stdio.
  - `src/contextweaver/adapters/gateway_error.py` — typed `GatewayError`
    dataclass with the §3.4 wire shape.
  - Two new runnable demos (`examples/mcp_gateway_demo.py`,
    `examples/mcp_proxy_demo.py`) wired into `make example`.
- **Content-addressed firewall idempotency** (#190).
  `ArtifactRef.content_hash` (lowercase sha256 hex) is populated when
  the firewall stores raw bytes.  `apply_firewall` now short-circuits
  when an incoming item already carries a populated `content_hash`,
  preventing the previous regression where a `build()` call run after
  `ingest_tool_result_sync()` overwrote the original raw bytes with the
  summary.

### Changed

- **`mcp` and `jsonschema` promoted to core dependencies** (was: planned
  optional extras).  Driven by the gateway / proxy runtimes — both are
  load-bearing for `gateway_spec.md` §4.4 argument validation and the
  MCP transport binding.  The AGENTS.md "minimal core runtime
  dependencies" rule is amended accordingly.
- **`mcp_tool_to_selectable` emits canonical `tool_id`** (§1.7
  cutover).  Existing call sites that hard-coded `f"mcp:{name}"` must
  consume the canonical form (round-tripped through
  `parse_tool_id` / `format_tool_id`).
- **`make_choice_cards` is token-native** against `cl100k_base`.  The
  `max_total_chars` and `max_desc_chars` keyword arguments are removed;
  callers use `target_tokens_per_card` and `hard_cap_tokens_per_card`
  (defaults 60 / 80 matching `gateway_spec.md` §2.3).

### Added (continued — earlier entries)

- **Gateway surface specification** (#30, #31). New
  `docs/gateway_spec.md` codifies the three contract gaps blocking the
  MCP proxy and gateway runtimes: canonical `tool_id` grammar
  (`{namespace}:{name}[@{version}][#{hash8}]` with a deterministic
  sha256-based hash over the input-schema shape), `ChoiceCard` size
  bounds expressed in exact `cl100k_base` `tiktoken` counts (target ≤
  60, hard cap ≤ 80 per card; banned fields enumerated), and the
  `tool_browse` path-navigation grammar (`/namespace/cluster/...` with
  reserved `*` segment and a fixed error shape). The spec also commits
  the proxy (#13) and gateway (#28) to a single schema-exposure
  strategy: stripped cards plus on-demand hydration via the existing
  `Catalog.hydrate` primitive — no `--full-schemas` opt-in. Two new
  bullets in `docs/agent-context/invariants.md` make the
  `ChoiceCard`-is-schema-free and `tool_id`-round-trip rules review
  blockers, and `mkdocs.yml` surfaces the spec as a top-level
  Gateway Spec page (sibling to Concepts and Cookbook).
- **Framework integration guides** for v0.6 (#77, #78, #79, #80).
  New pages under `docs/`: `integration_llamaindex.md`,
  `integration_langchain.md` (covers LangChain + LangGraph),
  `integration_openai_adk.md`, `integration_google_adk.md`, and
  `integration_pipecat.md`.  Each follows the existing
  `integration_mcp.md` / `integration_a2a.md` template (architecture
  diagram, minimal wiring, advanced patterns, troubleshooting) and uses
  the actual `ContextManager.ingest_tool_result_sync(tool_call_id, ...)`
  / `Router.route(...)` APIs throughout.  `README.md`'s two Framework
  Integrations tables are updated to link the new guides; the FAQ
  framework question is rewritten to match.
- **`docs/interop.md`** — "How contextweaver Fits" positioning page
  (#89).  Includes the policy-vs-execution framing, a runtime boundary
  ASCII diagram, a runtime interop matrix covering 10+ runtimes, three
  minimal integration snippets (routing-only, firewall-only, full
  pipeline), and an explicit non-goals section.
- **`docs/cookbook.md` + `examples/cookbook/`** — integration cookbook
  (#105) with four recipes: FastMCP routing, A2A multi-agent session,
  bring-your-own-tools, and firewall + drilldown.  Two new runnable
  scripts (`examples/cookbook/byot_recipe.py`,
  `examples/cookbook/firewall_drilldown_recipe.py`) are added to
  `make example`; the FastMCP and A2A recipes link to the existing
  `examples/fastmcp_adapter_demo.py` and `examples/a2a_adapter_demo.py`.
- **`mkdocs.yml` nav** — adds top-level "How contextweaver Fits" and
  "Cookbook" entries plus five new framework guides under the existing
  "Guides" section, and surfaces the existing `troubleshooting.md` in
  the nav.

## [0.3.0] - 2026-05-11

### Added

- **Minimal core dependencies and extras infrastructure** (#49, #50, #54, #55)
  - `pyproject.toml` `dependencies = ["tiktoken>=0.5", "PyYAML>=6.0", "rank-bm25>=0.2"]`
    — three small, broadly-used packages that unblock default behaviour the library
    would otherwise have to approximate (exact token counts, YAML configs, BM25 retrieval).
  - New optional extras: `[cli]` (rich), `[retrieval]` (rapidfuzz),
    `[ann]` (hnswlib, reserved), `[otel]` (opentelemetry), `[graph]` (networkx, reserved),
    `[all]` (union convenience).
  - mypy overrides for every new optional package so missing extras don't break type checks.
- **YAML catalog and graph support** (#54)
  - `contextweaver.routing.catalog.load_catalog_yaml()` — load a catalog from a YAML file.
  - `contextweaver.routing.catalog.load_catalog()` — auto-detect JSON vs. YAML by file
    extension (`.yaml` / `.yml` → YAML, anything else → JSON).
  - `save_graph()` / `load_graph()` in `routing.graph_io` now auto-detect format from
    the file extension and emit deterministic YAML (`sort_keys=True`).
  - `examples/sample_catalog.yaml` — runnable YAML version of `sample_catalog.json`.
- **BM25 and fuzzy retrieval backends** (#55)
  - `contextweaver._utils.BM25Scorer` — BM25 scorer backed by `rank-bm25` (core dep);
    same `fit` / `score` / `score_all` interface as `TfIdfScorer`.
  - `contextweaver._utils.FuzzyScorer` — fuzzy string-similarity scorer backed by
    `rapidfuzz`; available when `contextweaver[retrieval]` is installed,
    `FuzzyScorer is None` otherwise.
  - `Router(scorer_backend="bm25" | "tfidf" | "fuzzy")` — keyword-only parameter to
    select a scorer by name; default remains `"tfidf"` for backward compatibility.
    Unknown backend names raise `ConfigError`. Cooperates with the
    `engine_registry` / `retriever` plumbing from issue #47.
- **Production observability primitives** (#10)
  - New `contextweaver.metrics` module with `MetricsCollector` (thread-safe
    accumulator with `summary()` + `reset()`) and `MetricsHook` (concrete
    `EventHook` implementation that feeds a collector).
  - `ContextManager(metrics=...)` — optional `MetricsCollector` parameter; when
    present, full `RouteResult` is recorded after every routing call (capturing
    candidate count, top score, and confidence gap).
  - `ContextManager.metrics` property exposes the configured collector (or `None`).
  - Counters tracked: total builds, total routes, total prompt tokens, dedup
    removals, firewall interceptions, items excluded, budget overruns, and a
    merged `drop_reasons` map.
- **OpenTelemetry integration** (#57)
  - New `contextweaver.extras.otel.OTelEventHook` — `EventHook` implementation
    that emits OTel spans (`contextweaver.context.build`, `contextweaver.context.firewall`,
    `contextweaver.context.exclude`, `contextweaver.routing.route`) and metrics
    (`contextweaver.tokens.used` histogram, `contextweaver.firewall.interceptions` counter,
    `contextweaver.items.excluded` counter, `contextweaver.budget.exceeded` counter,
    `contextweaver.routing.candidates` histogram).
  - Available via `pip install 'contextweaver[otel]'`. Importing the module
    without the extra raises an `ImportError` carrying the exact install hint.
- **Enhanced CLI rendering via `[cli]` extra** (#52)
  - `__main__.py` `print-tree` subcommand uses `rich.tree` for coloured output
    when rich is installed (`pip install 'contextweaver[cli]'`);
    stdlib argparse + plain ASCII path remains byte-identical when the extra
    is absent.
- **Public API exports**
  - Top-level: `MetricsCollector`, `MetricsHook`, `BM25Scorer`, `FuzzyScorer`,
    `load_catalog`, `load_catalog_yaml`.
- **Routing — negative routing (#112).** `Router.route()` accepts new
  keyword-only `exclude_ids: set[str] | None` and `exclude_tags: set[str] | None`
  parameters that drop matching items before beam search.
  `RouteResult.excluded_count` reports how many items were filtered.
- **Routing — context-aware shortlisting (#116).** `Router.route()` accepts
  a new keyword-only `context_hints: list[str] | None` parameter; hints
  are appended to the scoring query without altering the catalog or graph.
- **Routing — toolset gating (#22).** `Router.route()` accepts new
  keyword-only `allowed_namespaces: set[str] | None` and
  `allowed_tags: set[str] | None` whitelists.  `RouteResult.gated_count`
  reports how many items were filtered.
- **Routing — `CatalogNormalizer` (#44).** New
  `contextweaver.routing.normalizer.CatalogNormalizer` and
  `NormalizationReport` apply deterministic metadata hygiene
  (case-insensitive tag dedupe, whitespace collapsing, namespace
  trimming, description fallback) to raw catalog imports.
- **Routing — `GraphManifest` (#48).** New
  `contextweaver.routing.manifest.GraphManifest` records build hash,
  seed, engine versions, timestamp, item count, strategy, and depth on
  every graph built by `TreeBuilder.build()`.  Survives
  `ChoiceGraph.to_dict()` / `from_dict()` round-trips.  Helper
  `compute_catalog_hash()` is exported from the top-level package.
- **Routing — incremental graph cache (#15).** `TreeBuilder.build()`
  caches built graphs by catalog hash.  Subsequent calls with an
  unchanged catalog return the cached graph in O(n) rather than
  rebuilding.  Use `use_cache=False` to force a rebuild;
  `clear_cache()` drops all cached graphs.
- **Routing — `RouteTrace` (#51).** New
  `contextweaver.routing.trace.RouteTrace` and `TraceStep` dataclasses.
  Always populated on `RouteResult.trace`; per-step beam expansions
  remain opt-in via `debug=True`.  The legacy
  `RouteResult.debug_trace` shape is preserved as a `@property` that
  delegates to `RouteTrace.to_legacy_dicts()` for backward compatibility.
- **Routing — uncertainty signals (#14).** `RouteResult` gains
  `is_ambiguous: bool` and `clarifying_question: str | None`.  Set when
  the rank-1/rank-2 gap is below the router's `confidence_gap`
  threshold; the question is rendered from the most distinguishing
  dimension (namespace or name) of the top candidates.
- **Routing — `EngineRegistry` (#47).** New
  `contextweaver.routing.registry.EngineRegistry` with `Retriever`,
  `Reranker`, and `ClusteringEngine` protocols on `protocols.py`.
  Bundled defaults: `TfIdfRetriever` (wraps `TfIdfScorer`),
  `NoOpReranker`, and `JaccardClusteringEngine`.  Module-level
  `default_registry` is pre-populated with the in-tree defaults;
  callers may register alternative engines under the `"retriever"`,
  `"reranker"`, and `"clustering"` slots.
- **Config — `Mode` enum and `ProfileConfig.mode` (#45).** New
  `contextweaver.profiles.Mode` enum with values `strict` (default),
  `seeded`, and `adaptive` (FUTURE placeholder).  `ProfileConfig`
  gains a `mode: Mode` field and an optional `seed: int | None` field;
  both round-trip through `to_dict()` / `from_dict()`.  Unknown mode
  strings on `from_dict()` raise `ConfigError`.
  `ProfileConfig.from_profile()` added as a backwards-compatible alias
  for `from_preset()`.
- **Config — `ContextManager.profile` (#45).** `ContextManager.__init__`
  accepts a keyword-only `profile: ProfileConfig | None` parameter that
  fills `budget`, `policy`, and `scoring_config` from the profile when
  per-arg overrides are not supplied.  New `ContextManager.profile` and
  `ContextManager.mode` properties expose the active profile and mode.
- **Routing — `TreeBuilder.routing_config` (#45).** `TreeBuilder.__init__`
  accepts a keyword-only `routing_config: RoutingConfig | None` parameter
  that populates `max_children`.  `Router` already accepted this
  parameter in v0.2.0.
- `ScoringConfig.dedup_threshold` field — exposes the Jaccard dedup threshold
  (default 0.85) via configuration; `ContextManager` now passes it through to
  `deduplicate_candidates()` (#182)
- `to_dict()` / `from_dict()` on `ContextPolicy`, `ContextBudget`, and
  `ScoringConfig` — completes the repo-standard serialisation methods on all
  config dataclasses (#184)
- `EpisodicStore` and `FactStore` protocols — formal `@runtime_checkable`
  protocol interfaces matching the `InMemory*` method signatures; `StoreBundle`
  type hints widened to protocol types (#40)
- `store/protocols.py` module — store-layer protocols (`EventLog`,
  `ArtifactStore`, `EpisodicStore`, `FactStore`) extracted from `protocols.py`
  to stay within the ≤300-line guideline; still importable from
  `contextweaver.protocols` and `contextweaver` for backward compatibility
- `profiles.py` module — `Mode`, `RoutingConfig`, and `ProfileConfig` live in
  `contextweaver.profiles` to stay within the ≤300-line guideline; importable
  from `contextweaver.profiles` and `contextweaver` (#179)

### Changed

- **Documentation: minimal-core-deps reframe** (#53)
  - README front-matter: `"zero runtime dependencies"` → `"minimal core dependencies"`,
    `"deterministic output"` → `"deterministic by default"`.
  - README installation section gains an extras table covering every optional
    capability shipped today.
  - `AGENTS.md` style rule rewritten: zero core runtime deps + extras model;
    new core deps require broad ecosystem use, small wheel, and unblocked
    default behaviour.
  - `CONTRIBUTING.md` and `.github/copilot-instructions.md` updated to match.
- `TiktokenEstimator` simplified — `tiktoken` is now a core dep, so the
  try/except-stub fallback is gone. The estimator still degrades gracefully
  to `CharDivFourEstimator` when the tiktoken encoding download fails (offline
  / air-gapped environments) and logs a warning naming `TIKTOKEN_CACHE_DIR`
  as the workaround.
- `Router.__init__` now raises `ConfigError` (a `ContextWeaverError`
  subclass) instead of bare `ValueError` when `confidence_gap` is
  outside `[0.0, 1.0]`.
- `RouteResult.trace` is the new authoritative trace surface;
  `RouteResult.debug_trace` is preserved as a backwards-compatible
  property delegating to `trace.to_legacy_dicts()`.
- `TreeBuilder.build()` now records the effective `max_children` under
  `manifest.extra["max_children"]`, honouring the docstring contract
  that the value is persisted on the graph manifest.
- `Router.__init__` accepts new keyword-only `retriever: Retriever | None`
  and `engine_registry: EngineRegistry | None` parameters so the
  pluggable `EngineRegistry` from issue #47 is now wired end-to-end.
  The legacy `scorer: TfIdfScorer | None` parameter is still accepted
  and is transparently wrapped in an internal `Retriever` adapter.
- `TreeBuilder.__init__` accepts new keyword-only
  `clustering: ClusteringEngine | None` and
  `engine_registry: EngineRegistry | None` parameters; the
  cluster-grouping strategy now delegates to the configured engine
  (default: `JaccardClusteringEngine` from `default_registry`) instead
  of the previous inline algorithm.  Rebalancing of oversized clusters
  remains the builder's responsibility.
- `Retriever` protocol now exposes `score_one(query, index) -> float`
  for per-document scoring at arbitrary corpus indices (used by the
  Router beam search).  The bundled `TfIdfRetriever` implements it.
- `RouteResult` exposes two new fields — `context_hints: list[str]`
  and `context_boost_applied: bool` — so callers can introspect
  whether the issue #116 context-hint augmentation actually altered
  the scoring query.  The same values round-trip on
  `RouteTrace.extra["context_hints"]` / `extra["context_boost_applied"]`.
- `RouteTrace.retriever_engine` is now populated from the resolved
  engine name instead of being hard-coded to `"tfidf"`.
- `ProfileConfig.to_dict()` / `from_dict()` now include `policy` (previously
  excluded because `ContextPolicy` lacked serialisation); docstring expanded
  to make the round-trip contract explicit (#184)
- `ContextManager.episodic_store` / `fact_store` properties now return protocol
  types (`EpisodicStore` / `FactStore`) instead of concrete `InMemory*` types (#40)
- `StoreBundle.to_dict()` / `from_dict()` docstrings now spell out the
  silent-`None` round-trip behaviour for custom backends that lack a
  `to_dict()` method
- `Mode`, `RoutingConfig`, and `ProfileConfig` are not re-exported from
  `contextweaver.config`; import them from `contextweaver.profiles` or the
  top-level `contextweaver` package. Dropping the re-export resolves a
  circular-import smell between `config.py` and `profiles.py`

### Fixed

- Routing exclusions (`exclude_ids` / `exclude_tags`) and toolset
  gating (`allowed_namespaces` / `allowed_tags`) now happen
  pre-scoring rather than only at result collection time.  Previously
  excluded leaf nodes could consume beam slots and prevent eligible
  siblings from being explored under tight `beam_width`.  The router
  now skips ineligible children (and internal nodes whose entire
  subtree was filtered out) before scoring.
- `RouteResult.is_ambiguous` and `RouteTrace.runner_up_score` are now
  computed from the untrimmed sorted view of beam-search results, so
  callers using `top_k=1` still see the uncertainty signal introduced
  by issue #14.
- `routing.normalizer` module docstring now accurately describes
  lenient-mode item drops (blank or duplicate IDs are dropped to
  `report.invalid_ids`) instead of claiming the normalizer never
  drops items.
- Replaced 3 bare `ValueError` raises in `context/sensitivity.py` with
  `PolicyViolationError` / `ConfigError` (#183)
- Replaced bare `ValueError` in `routing/router.py` (`confidence_gap` validation)
  with `ConfigError` (#183)
- `BM25Scorer.fit()` now feeds `BM25Okapi` a per-document token list that
  preserves term frequency. The previous implementation called
  `sorted(tokenize(doc))` on a `set`, which collapsed duplicates and degraded
  BM25 to a binary-match scorer. A new `contextweaver._utils.tokenize_list()`
  helper applies the same normalisation pipeline as `tokenize()` but returns
  a `list[str]`; `tokenize()` now delegates to it for the unique-set view.
  (review #188)
- `MetricsCollector` no longer accumulates per-route values in unbounded
  lists. Route-level statistics are tracked as running sums + maxima so
  memory stays O(1) in long-running processes. `summary()` keys are
  preserved and gain three new entries (`max_candidates_per_route`,
  `max_top_score`, `max_confidence_gap`). (review #188)
- `OTelEventHook` now records `contextweaver.tokens.used` as a histogram
  instead of a gauge. The synchronous `create_gauge` instrument only
  landed in `opentelemetry-api>=1.27`, but the `[otel]` extra pins
  `>=1.20`. Histograms are portable across the entire supported range and
  give callers per-build token distributions instead of a latest-value
  cache. (review #188)

### Notes

- `_utils.py` (392 lines), `routing/catalog.py` (~410 lines) and `routing/router.py`
  (~400 lines) are over the 300-line module guideline. These files were already
  approaching or over the limit before this PR; decomposition is tracked under
  the routing-pipeline epic (#56).
- `EventHook.on_route_completed` retains its `list[str]` (tool ids) signature for
  backward compatibility. Full `RouteResult` metrics (top score, confidence gap)
  flow through `ContextManager.metrics` instead of the hook.
- `Mode.adaptive` is currently a forward-compatible placeholder — no
  pipeline stage is conditioned on the mode value yet.  Selecting
  `Mode.adaptive` is accepted but has no behavioural effect.
- `TreeBuilder.build()` writes a deterministic `timestamp=0.0` to its
  manifest so that two builds of identical inputs produce identical
  graphs (per the AGENTS.md "Deterministic by default" invariant).
  Callers wanting a wall-clock timestamp can replace the manifest via
  `graph.manifest = GraphManifest.for_build(items)` after build.
- `RouteResult.trace.steps` is empty unless `debug=True`; the rest of
  the trace (top scores, ambiguity, exclusion / gating counts) is
  always populated.

## [0.2.0] - 2026-04-17

### Added
- `.github/prompts/add-feature.prompt.md`, `.github/prompts/fix-bug.prompt.md`, and `.github/prompts/refactor-module.prompt.md` — reusable step-by-step agent workflows for common tasks (feature addition, bug fixing, module refactoring), each with explicit `_Success:` criteria and `make ci` as the final gate (#96)
- `SECURITY.md` — vulnerability disclosure policy covering supported versions, GitHub Security Advisories channel, response timeline, and security scope (context firewall, prompt injection, adapter input validation, deserialization)
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

### Changed
- `make test` now runs `pytest --cov=contextweaver --cov-report=term-missing -q` (non-gating coverage report); updated `AGENTS.md`, `docs/agent-context/workflows.md`, and `.claude/CLAUDE.md` to match (#165)
- Coverage config: removed redundant `omit` pattern (already excluded by `source` scope), added `branch = true` for branch coverage visibility, tightened `"if __name__"` exclusion regex to `"if __name__ == ['"]__main__['"]"` (#165)
- CI: added pip dependency caching (`actions/cache@v4`) to speed up the Python matrix build (#94)

### Fixed
- Normalize example output markers to ASCII so `make example` works on Windows consoles using cp1252 encoding
- `examples/langchain_memory_demo.py` — replaced all non-ASCII output characters (`─`, `—`, `←`) with ASCII equivalents (`-`, `--`, `<-`) to prevent `UnicodeEncodeError` on Windows cp1252 consoles

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
- Named configuration presets in `config.py` (#133)
  - `RoutingConfig` dataclass bundling `beam_width`, `max_depth`, `top_k`, `confidence_gap`, `max_children`; includes `routing_kwargs()`, `to_dict()`, `from_dict()`
  - `ProfileConfig` dataclass bundling `budget`, `policy`, `scoring`, `routing`; includes `from_preset()``, `to_dict()`, `from_dict()`
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
  - `EventLog` exposes `all()` / `filter_by_kind()` / `count()` / `tail()`, not `list()`
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

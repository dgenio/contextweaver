# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

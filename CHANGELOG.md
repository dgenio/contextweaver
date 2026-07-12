# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.17.0] - 2026-07-12

### Added

- **CI/CD & dependency-hygiene hardening cluster (#747, #748, #749, #750, #751,
  #754, #755, #475, #756, #757).** A coordinated batch of infrastructure fixes:
  - **Python 3.14 support (#754).** Split the heavy framework-adapter tree into
    a new `[dev-adapters]` extra so `[dev]` resolves on 3.14; added a gating
    3.14 CI cell running fmt/lint/type/test.
  - **Windows support (#749).** `atomic_write` now retries `os.replace` under
    the Windows `PermissionError` (WinError 5); Agent Skills resource paths and
    generated MCP configs are POSIX-normalized; added a `windows-latest` CI cell.
  - **Store correctness (#750).** The async→sync bridge bounds each operation
    with a timeout, raising the new `StoreTimeoutError` instead of hanging; the
    `make type` gate is hermetic (redis type-checked for real, dead mypy
    overrides pruned).
  - **Coverage ratchet + property tests (#475, #755).** A `fail_under` branch
    coverage floor gated on the 3.12 cell; Hypothesis property tests for
    secret-scrubbing, token estimators, canonical JSON, and episode clustering.
  - **CI-dark extras (#751).** A scheduled `ci-dark-extras` job installs the
    previously-untested adapter/backend extras and runs them under a new
    `--strict-live` flag; Zep fake tests are decoupled from the SDK import.
  - **Release-metadata gate (#747).** `server.json` and `CITATION.cff` versions
    are gated against `pyproject.toml` (and corrected from 0.15.0 / 0.10.0);
    `server.json` is regenerated at publish time.
  - **Workflow hardening (#748).** SHA-pinned write-privileged workflows,
    job-scoped permissions, un-silenced the benchmark-scale job, and renamed
    `scorecard-weekly.yml` → `benchmark-scorecard-weekly.yml`.
  - **weaver-spec pin & docs hygiene (#757).** Pinned the conformance fetch to
    `v0.7.0`, removed the dead Puppetmaster integration doc, fixed two nav
    orphans.

- **MCP gateway platform-maturity cluster (epics #365, #376, #385).** A large
  batch advancing the gateway from a local optimizer toward a governable,
  observable, model-assistable platform. Every model-backed feature is opt-in,
  deterministic-by-default, and governed by the new deterministic-first rubric
  (`docs/agent-context/model-backed-features.md`, #505):
  - **Catalog governance.** Inventory metadata (owner/domain/risk/side-effects/
    lifecycle/environment/tier) under the reserved `_contextweaver.inventory`
    namespace (#377); audience-scoped visibility profiles filtering the tool
    surface before ChoiceCards, fail-closed on allowlists (#379); a collision /
    duplicate-capability analyzer (#381); catalog pinning against tool-surface
    drift (#656); `catalog diff` with routing-impact measurement (#514).
  - **Observability.** A versioned JSONL telemetry handoff contract with a
    published envelope schema for downstream analytics (#382); a tool-surface
    health scorecard (#380); `mcp status` runtime surface (#655); a read-only
    `mcp ops` live-triage view (#668); an SLO starter kit (#666); a what-if
    churn/traffic simulator (#662); a record/replay wire-transcript harness
    (#654).
  - **Model assist.** A local embedding-model setup doctor (#386); a semantic
    vector index over catalogs (#387); a telemetry-trained tool reranker behind
    the new `[ranker]` extra (#388); offline LLM catalog-metadata enrichment
    suggestions (#383); auditable LLM firewall summaries with provider
    provenance (#384); an MCP-sampling-backed `call_fn` (#623); an advice-only
    AdvisorPack escalation (#741); a shared LLM call-guard envelope (#494).
  - **Transport & lifecycle.** Streamable HTTP transport for the gateway and
    proxy plus a readiness conformance suite (#422, #665); live catalog refresh
    on `tools/list_changed` (#424); graceful shutdown semantics (#626); a
    standalone `mcp memory-serve` server exposing episodic/fact stores (#632).
  - **Interop & ops tooling.** Provider-native tool exporters
    (`to_openai_tools` / `to_anthropic_tools` / `to_gemini_function_declarations`)
    for routed shortlists (#609); an MCP spec-compatibility matrix (#548);
    a `mcp doctor` preflight command (#395); a `visualize` HTML report command
    (#442); an official multi-arch Docker image pipeline (#432).
- `FirewallStats.llm_provider` records provider/model metadata when an
  LLM-backed summarizer produced the summary (#384).

- **Live multi-upstream MCP gateway serving (#366, #368, #374).** `contextweaver
  mcp serve --config gateway.yaml` now supports an `upstreams:` block that
  launches and proxies real upstream MCP servers — `type: stdio` (child
  process), `type: http` (streamable HTTP, the current MCP-spec transport),
  or `type: sse` (legacy) — instead of only the static-catalog/stub path,
  which remains unchanged and still works with no `upstreams:` block. Each
  upstream supports a `namespace` prefix (feeding the existing canonical
  `tool_id` namespace inference with no other code changes), `include_tools`
  / `exclude_tools` glob filters evaluated against the upstream's own tool
  names, and a per-upstream `timeout`. A new `startup:` block
  (`contextweaver.adapters.startup_policy.StartupPolicy`) controls
  fault-tolerant startup: `mode: degraded` (default) starts with whatever
  upstreams come up healthy as long as `min_healthy_upstreams` is met and no
  `required` upstream failed; `mode: strict` aborts startup on any required
  upstream failure. A `StartupReport` records the resolved status
  (`loaded`/`failed`/`timed_out`) and tool count per upstream, plus
  deterministic tool-name collision diagnostics, logged to stderr at
  startup. New `contextweaver.exceptions.UpstreamStartupError`
  (`CW_UPSTREAM_STARTUP`) carries the report. New modules:
  `contextweaver.adapters.upstream_config` (`UpstreamSpec`,
  `parse_upstreams_config`), `contextweaver.adapters.startup_policy`
  (`StartupPolicy`, `StartupReport`, `UpstreamStatus`,
  `detect_tool_name_collisions`), `contextweaver.adapters.upstream_launch`
  (`launch_upstreams`, `NamespacedFilteredUpstream`) — composing the
  existing `McpClientUpstream` / `MultiplexUpstream` primitives rather than
  duplicating them. Resources/prompts are not yet supported over live
  upstreams (tools only); serve-side Streamable HTTP (as opposed to
  upstream-side, added here) remains tracked separately under #422.
  `catalog` and `upstreams` are mutually exclusive: rejected both in a
  config file and as a command-line `--catalog` paired with an `upstreams`
  config (rather than silently ignoring the flag). Include/exclude glob
  filtering is centralised in a shared `upstream_config.tool_matches_filters`
  helper so `UpstreamSpec.matches_tool` (tested) and the runtime
  `NamespacedFilteredUpstream` filter can never diverge.
- **`contextweaver mcp import-vscode` (#367).** Migrates an existing VS
  Code-family MCP config (`servers` or `mcpServers`) into a gateway
  `upstreams:` config plus a replacement client config exposing only
  `contextweaver-gateway`. Defaults to `--dry-run` (prints the plan without
  touching any file); `--write` applies it, backing up the original output
  file by default (`--no-backup` to opt out). Servers with an unsupported
  shape (no `command`/`url`) are skipped with a warning rather than
  aborting the whole migration. New `contextweaver._vscode_import` module.
- **Artifact TTL and redaction-before-store for `mcp serve --state-dir`
  (#375).** `JsonFileArtifactStore` (the backend `--state-dir` uses) gains
  optional `ttl_seconds` (process-lifetime-scoped expiry, lazily evicted on
  read and swept before quota checks on write — not persisted across a
  restart; issue #617 tracks making that durable) and `redact_secrets`
  (scrubs UTF-8 content with `contextweaver.secrets.scrub_secrets` *before*
  writing, so the persisted `content_hash` matches the redacted bytes;
  binary content is stored unchanged). Wired into `mcp serve` via a new
  `artifacts:` config block (`contextweaver.adapters.artifact_policy.ArtifactPolicy`:
  `ttl_seconds`, `max_bytes`, `max_artifacts`, `redact_secrets`) alongside
  the existing `max_bytes`/`max_artifacts` quotas. Note: this narrows the
  original issue's scope — the `tool_view` policy gate and size-quota
  machinery it also asked for already shipped via issues #373/#746/#497;
  implementing them again here would have duplicated that shipped code.
  `artifacts.redact_secrets` is deliberately independent of the `--redact`
  prompt-time firewall: a secure-by-default gateway with `--state-dir` still
  persists raw artifact bytes unless `artifacts.redact_secrets: true` is set
  (documented in `docs/gateway_spec.md` §4.7).
- **Gateway policy presets (#664).** New `contextweaver.adapters.gateway_presets`
  module: `GatewayPreset.from_preset("safe" | "balanced" | "throughput")` bundles
  the authorization policy (`ToolPolicy`), retry policy, rate limits, and the
  new `CacheConfig` into one named starting point. `safe` requires approval on
  every `tool_execute` call regardless of the (unverified) upstream read-only
  hint; `balanced` is allow-all with moderate quotas; `throughput` is allow-all
  with no quotas and read-only caching on. Selectable via `mcp serve
  --policy-preset <name>` or the `policy_preset` config key; an explicit
  `policy` / `retry` / `rate_limits` / `cache` config block still wins over the
  preset **for that block** (block-level override, not a field merge).
  Selecting no preset is inert — behaviour is byte-identical to today. New
  `mcp serve --print-effective-policy` prints the resolved (preset-or-overridden)
  bundle as deterministic, sorted JSON via `GatewayPreset.to_dict()` and exits,
  without requiring the catalog to exist on disk. `CacheConfig.allow` accepts
  any iterable of `tool_id` strings and is normalised to a `frozenset`, so the
  runtime value matches its annotation and instances stay hashable. Exported
  from `contextweaver.adapters`. Documented in `docs/security_mcp_gateway.md`.
- **Knowledge-bundle context sources: OKF, repository knowledge, lessons, and
  expertise packs (#736, #763, #767, #776).** Four adapters let contextweaver
  ingest external knowledge stored as OKF-style Markdown-plus-YAML-frontmatter
  bundles and expose it as bounded, selectable `ContextItem` candidates that
  flow through the existing candidate-selection/budget/dedup/sensitivity/
  rendering pipeline unchanged (no pipeline stage was modified; no new
  `ItemKind` was introduced — nodes materialise as `doc_snippet` with
  provenance under the `_contextweaver` metadata namespace).
  - **OKF bundle loader (#736).** `contextweaver.adapters.okf.load_okf_bundle`
    parses a directory of OKF concept files; `index.md`/`log.md` are
    recognised bundle metadata/history, excluded from concept content by
    default. `select_knowledge` ranks and packs nodes deterministically
    against a query and token budget.
  - **Repository knowledge (#763).** `contextweaver.adapters.repo_knowledge`
    narrows the OKF loader to generated repo docs: plain Markdown without
    frontmatter still loads (filename-derived title), `max_files`/
    `max_total_bytes` guardrails bound large trees, and `classify_usage`
    assigns deterministic usage tags (`"debugging"`, `"onboarding"`, ...) —
    plain metadata strings, not `Phase` enum values. Links are never
    auto-followed, so a documentation tree cannot force-load content beyond
    its own root.
  - **Lessons (#767).** `contextweaver.adapters.lessons` adds
    lifecycle-aware eligibility on top of the shared parsing core:
    `LessonSelectionPolicy` excludes `rejected`/`deprecated` lessons and
    unreviewed `candidate` lessons by default (opt in via
    `include_candidates=True`), and every exclusion is reported with an
    explicit reason (`select_lessons` → `(items, exclusions)`).
  - **Expertise packs (#776).** `contextweaver.adapters.expertise_pack`
    validates pack *structure* (an `index.md` declaring `version`, every
    node carrying a `key`) rather than the full external weaver-spec schema
    (`dgenio/weaver-spec#184`, tracked as a follow-up seam) and adds
    deterministic conflict detection (`detect_conflicts`): constraints
    sharing a `key` that disagree on text, scoped by `task_tags` and
    liveness — no LLM-backed contradiction inference.
  - The four adapters' private parsing core (`adapters/_okf_io.py` +
    `_okf_materialize.py`) is not re-exported at the `contextweaver.adapters`
    package level — mirroring how the `mcp_primitives`/`gateway_primitives`
    family is kept at submodule level — since `adapters/__init__.py` is a
    frozen, grandfathered module already at its size ceiling. Import
    directly from each submodule, e.g.
    `from contextweaver.adapters.okf import load_okf_bundle`.
  - See `docs/recipes/okf_bundle.md` and
    `examples/knowledge_bundles_demo.py` for a runnable walkthrough of all
    four adapters.

- **Secret-scrubbing parity across every prompt-bound surface (#742, #743, #745).**
  A coordinated pass closing three gaps where secret scrubbing reached only one
  of several parallel paths:
  - **Broader secret detection (#742).** `secrets.py` gains provider/SaaS token
    patterns — OpenAI (`sk-…`/`sk-proj-…`), Anthropic (`sk-ant-…`), GitHub
    fine-grained PATs (`github_pat_…`), Slack app tokens (`xapp-…`), Stripe
    (`sk_live_`/`rk_live_`), and SendGrid (`SG.…`) — so `contains_secret` and
    `scrub_secrets` catch the credential classes most likely to appear in
    AI-agent tool output. New `scrub_secrets_in_obj` recursively scrubs string
    leaves of a JSON-like value with its shape unchanged. The zipimport catalog
    cache moves from world-writable `tempfile.gettempdir()/contextweaver/` to a
    per-user cache dir (`$XDG_CACHE_HOME`/`~/.cache`) with an ownership check
    before reuse (catalog-poisoning / TOCTOU hardening on shared POSIX hosts).
  - **Resource/prompt gateway browse scrubbing (#743).** `PrimitiveGatewayRuntime`
    / `PrimitiveIndex` now accept `redact_secrets`, matching the tool
    `ProxyRuntime`, so resource/prompt ChoiceCards are scrubbed on the same
    runtime. The query/path card-production logic is unified into one shared
    `adapters/_bounded_browse.bounded_browse` helper that both runtimes call, so
    future card-path hardening can no longer reach only one copy. `mcp serve`
    threads its `--redact` choice into the primitive runtime. The tool path is
    behavior-preserving (its `top_k` guard stays in the primitive wrapper).
  - **Sidecar `/v1/compact` scrubbing (#745).** `compact_tool_result` gains a
    `redact_secrets` option that scrubs the pass-through payload (string leaves,
    shape unchanged), the text summary, the structured projection, and extracted
    facts (the offloaded raw artifact is left intact). It is threaded through
    `CompactRequest`, `SidecarConfig` (server-side default), and the published
    `schemas/sidecar/v1/compact_request.schema.json`. Defaults off on every
    surface (posture owned by #744).
- **Runtime authorization / policy gate for the MCP gateway (#373).** New
  `ToolPolicy` (ordered `PolicyRule`s + default action) evaluated inside
  `ProxyRuntime.execute` after schema validation and **before** any upstream
  dispatch, and — via `meta_tool="tool_view"` rules — before `tool_view` raw
  artifact egress (#746). Actions are `allow` / `deny` (typed `POLICY_DENIED`
  error, upstream never called) / `require_approval` (typed `AUTH_REQUIRED`
  error for host sign-off). Matching is pure/deterministic on namespace, a
  case-sensitive tool glob, tags, `read_only`, and surface. Default is allow-all
  (inert), so existing deployments are unchanged; configure it under the
  `policy` key of `mcp serve --config` or `ProxyRuntime(policy=...)`. Exported
  from `contextweaver.adapters`; two new `GatewayError` codes (`POLICY_DENIED`,
  `AUTH_REQUIRED`) added to `gateway_spec.md` §3.4.
- **Secure-by-default serving posture (#744).** `contextweaver mcp serve` now
  enables the `HeuristicSensitivityClassifier` and secret scrubbing
  (`redact_secrets`) by default, so unlabelled tool output carrying
  secret/PII-shaped content is classified and scrubbed before it reaches a
  prompt. Opt out with `--no-redact` (or `redact: false` in config), which
  prints a loud startup warning. The HTTP sidecar prints a startup warning when
  bound without `--api-key`. The library-level `ContextManager` defaults are
  unchanged; the decision is recorded in `docs/security_model.md`.
- **Operator security docs.** New `docs/security_mcp_gateway.md` (least-privilege
  gateway operation: secrets, destructive tools, the policy gate; #372) and
  `docs/sensitivity.md` (sensitivity levels, floor/action, redaction hooks,
  verification, and limits; #640), linked from the docs nav and
  `security_model.md`.
- **MCP incident packs (#661).** Added `contextweaver mcp incident-pack` to
  create offline, redacted triage zip bundles with a machine-readable manifest,
  config/catalog summaries, diagnostics summaries, redacted source excerpts,
  and a reproducible command checklist. Command logs are included only via an
  explicit `--command-log` file; shell history is never collected automatically.
  Truncation is now flagged from the emitted redacted bytes (not just raw file
  size), so redaction/pretty-print expansion can no longer mark a sliced entry
  as complete; the `--max-file-bytes` help clarifies the cap applies to redacted
  content before the truncation marker; and CLI errors no longer misattribute
  every failure to `--out`. Manifest warnings are now scrubbed with
  `scrub_secrets` like every other emitted string, so raw paths and OS error
  text can no longer leak into an otherwise-redacted bundle. Structured
  config/catalog/diagnostics inputs are key-redacted from the full parsed
  document rather than a size-capped window, so a value under a sensitive key
  (e.g. `password`) can no longer leak when the source exceeds the decode
  window; and filesystem failures (e.g. an unwritable `--out`) now surface as a
  clean CLI error instead of an uncaught traceback.
- **Deployment-intent onboarding wizard (#660).** `contextweaver start` now guides
  first-run users through four explicit paths (`gateway`, `library`, `routing`, or
  `integration`). One local prompt—or the scriptable `--profile` option—prints an
  exact next-command sequence, a configuration hint, a short verification checklist,
  current gateway-runtime boundaries, and the relevant canonical guide. The command
  is deterministic, network-free, and makes no filesystem changes.

- **Benchmark-suite maturation: scaling, scenarios, CI gating, and trend
  (#369, #418, #491, #554, #687, #688).** A coordinated pass on the benchmark
  subsystem, all deterministic and offline:
  - **Large-catalog benchmark (#369).** `make benchmark-large-catalog`
    (`benchmarks/large_catalog.py`) routes over 300+ tools across 8 namespaces
    with near-duplicate distractor variants and destructive (side-effecting)
    tools, reporting recall@1/3/5, MRR, ChoiceCard-vs-naive prompt-token
    reduction, namespace/deny filtering, character counts, and a large-result
    firewall + artifact-view round trip. Writes a
    committed scorecard (`benchmarks/large_catalog_scorecard.md`, latency
    excluded for determinism) plus `benchmarks/results/large_catalog.json`;
    `--check` gates scorecard drift and `--strict` gates regression-guard
    thresholds.
  - **Scenario benchmark (#418).** `make benchmark-scenario`
    (`benchmarks/scenario_routing.py`) contrasts naive all-tools prompting
    against bounded `ChoiceCard` routing across tool-heavy scenarios
    (`benchmarks/scenarios/routing_choicecard.json`), reporting
    correct-in-top-k, rank, cards shown, deterministic route work, ambiguity/
    clarification behavior, destructive-card exclusion, token reduction, and raw-
    result firewall exposure to a committed report (`benchmarks/scenario_routing.md`).
  - **Quality-regression gate (#491).** `scripts/benchmark_gate.py` +
    `benchmarks/gating.yaml` turn the informational benchmark delta into a
    gating CI check against the target-branch baseline: a PR that regresses
    recall@k / MRR / precision@k / token-savings / compaction ratio, or drops
    a previously-gated cell, fails the new `benchmark-gate` CI job. The
    fixed-fixture smoke evaluation is also a required check on Python 3.12.
    Latency is never gated; the `benchmark-accepted` PR label downgrades a
    failure to a warning for intentional trade-offs.
  - **Release trend (#554).** `scripts/render_trend.py` captures a
    deterministic, latency-free metric snapshot per release under
    `benchmarks/results/history/<version>.json` and renders the
    release-over-release view to `benchmarks/trend.md` (`make trend` /
    `make trend-check`); the publish workflow requires the tagged version's
    snapshot and a drift-free trend before publishing.
  - **Scaling matrix docs (#687).** `docs/benchmarks/scaling-matrix.md`
    documents the 10k-tool scaling methodology, reproducible commands, and
    result interpretation, tying together the routing-scale, large-catalog,
    and per-backend matrix benchmarks.
  - **Scheduled routing-scale smoke (#688).** A non-gating
    `.github/workflows/benchmark-scale.yml` runs the routing-scale profiler on
    a weekly schedule and uploads its JSON + report as a per-run trend
    artifact.

- **Multi-client MCP config-pack generator (#659).**
  Added `contextweaver mcp generate-configs` to render client recipe files
  (`copilot_mcp.json`, `cursor_mcp.json`, `claude_desktop_config.json`,
  `claude_code_mcp.json`) from one canonical `mcp serve --config` input.
  The command reuses `mcp serve` config validation, supports target selection,
  fails closed on unknown/invalid target values, blocks overwriting unless
  `--force`, emits target-specific compatibility warnings, and produces
  deterministic JSON artifacts suitable for committing. Added CLI tests for
  generation behavior and fixture-shape pinning.

- **Supply-chain & security CI hardening (#443, #689, #690, #691, #692, #468, #552).**
  A coordinated security-posture pass under the supply-chain hardening umbrella (#443):
  - **CodeQL** code scanning (`.github/workflows/codeql.yml`) with the
    `security-extended` query pack, on PR, `main`, and a weekly schedule (#689).
  - **pip-audit** dependency scanning (`.github/workflows/pip-audit.yml`):
    gating on the core runtime dependency set, report-only for the heavier dev
    extra (#689).
  - **OpenSSF Scorecard** analysis (`.github/workflows/ossf-scorecard.yml`)
    with results published to code scanning and a README badge; the OpenSSF
    Best Practices badge application is tracked as a manual step (#552).
  - **Dependabot** (`.github/dependabot.yml`) weekly `pip` and `github-actions`
    updates, grouped to limit noise (#443).
  - **Release-integrity gate** in `publish.yml` (#468): a `verify` job asserts
    the release tag matches the `pyproject.toml` version, runs the test suite,
    and `twine check`s the built distribution before the publish job runs.
  - **Build-provenance attestations** for released artifacts via
    `actions/attest-build-provenance` (#690).
  - **`security-policy-check`** gate (`scripts/check_security_policy.py`, wired
    into `make ci` and `ci.yml`): fails when `SECURITY.md`'s supported-version
    table drifts from the package version or links a missing doc. Refreshed the
    supported series to `0.16.x` (#691).
  - **Security tooling runbook** (`docs/security_tooling.md`) documenting the
    triage SLA, ownership, and the false-positive exception process (#692).
- `scripts/check_readme_version.py` gained a `--print-version` flag so the
  release-integrity gate reads the package version through the same single
  source of truth as the drift guard.

### Changed

- **`rank-bm25` demoted to the `[bm25]` optional extra (#756).** A plain install
  no longer ships it; selecting the BM25 scorer backend without the extra raises
  the standard helpful missing-dependency error. **Breaking** for callers that
  used `scorer_backend="bm25"` without installing `contextweaver[bm25]`.

### Fixed

- **Live catalog refresh no longer deadlocks the session receive loop (#424).**
  `make_message_handler` now *schedules* `LiveRefresher.on_list_changed` as a
  background task (`schedule_on_list_changed`) instead of awaiting it inline —
  the handler runs inside the MCP `ClientSession` receive loop, and the
  refresh's own `tools/list` needs that loop free to deliver its response.
  In-flight tasks are tracked (`pending_tasks`, `wait_idle`) so they are
  drained on shutdown and never garbage-collected mid-flight.
- **`routing/catalog_diff.py` no longer imports the async context layer.**
  Recall@k is computed by a local helper mirroring `eval.metrics.recall_at_k`
  instead of importing `contextweaver.eval.metrics` (whose package `__init__`
  pulls in `eval.context` → `context.manager`), preserving the sync-only
  `routing/` ↔ `context/` boundary (#514).
- **Telemetry contract now rejects version drift (#382).**
  `validate_event_dict` flags events whose `version` differs from
  `TELEMETRY_CONTRACT_VERSION`, so `read_jsonl` skips non-v1 events instead of
  silently returning them as conforming.

## [0.16.0] - 2026-06-21

### Added

- **SSE transport for MCP gateway and proxy (#694).**
  `McpGatewayServer.run_sse()` and `McpProxyServer.run_sse()` bind the
  existing server surfaces onto an HTTP/SSE endpoint using the MCP SDK's
  `SseServerTransport` and `uvicorn`.  The `mcp serve` CLI gains
  `--transport stdio|sse`, `--host`, and `--port` (config-file compatible).
  Default remains **stdio** for backward compatibility; SSE is opt-in.  SSE
  enables the MCP SDK's DNS-rebinding protection (off by default in the SDK),
  scoping the `Host`/`Origin` allowlist to the bound `--host`/`--port`.  A
  transport & compatibility matrix is added to `docs/integration_mcp.md`
  documenting tested client versions (Claude Desktop, Claude Code,
  VS Code Copilot, Cursor) and their supported transports.  No new
  dependencies — starlette and uvicorn are already pulled by the MCP SDK.

- **Memory consolidation engine (#498, #679, #680, #681, #682, #683).**
  New `contextweaver.context.consolidate(...)` distills episodic memory into
  durable, deduplicated, provenance-stamped facts. The deterministic core
  clusters similar episodes (`cluster_episodes`, #679), promotes clusters that
  meet `ConsolidationPolicy` thresholds (`min_occurrences` / `min_sessions`)
  into `PromotedFact` records carrying full source provenance and the **maximum**
  source sensitivity (`promote_clusters`, #680), and reports entries past the
  decay horizon without deleting them — the stores are append-only
  (`decay_episodes` / `decay_facts`, #681). An optional, fail-closed `call_fn`
  may refine a fact's canonical text, rejecting any completion that introduces
  ungrounded tokens or a negation absent from the source notes (#682). `consolidate(..., apply=True)` upserts the promoted
  facts with content-addressed IDs, so re-running over an unchanged store is a
  no-op (idempotent). Results are returned as a `ConsolidationReport`
  (serialisable via `to_dict`/`from_dict`). New public surface in
  `contextweaver.context`: `consolidate`, `cluster_episodes`, `promote_clusters`,
  `decay_episodes`, `decay_facts`, `ConsolidationPolicy`, `ConsolidationReport`,
  `PromotedFact`, `EpisodeCluster`. A new `contextweaver consolidate` CLI
  subcommand runs the pipeline over JSON-serialised stores. Quality is
  measurable offline via `contextweaver.eval.evaluate_consolidation` →
  `ConsolidationEvalReport` (precision / coverage + dedup ratio, #683). Pure
  stdlib; no new dependency.

- **Package metadata drift guard (#473).** The existing
  `readme-version-check` now also verifies that Python version classifiers in
  `pyproject.toml` match the gating CI matrix, preventing PyPI metadata from
  lagging the tested support range. Package metadata now advertises Python 3.13
  support, removes the long-expired no-op `[cli]` extra, and drops reserved
  `[ann]` / `[graph]` extras that installed dependencies without activating any
  runtime code.

- **Routing-scale index cache + profiler (#543, #624, #685, #684, #686).**
  New `contextweaver.routing.RoutingIndexCache` + `CachedRetriever` persist and
  reuse the fitted first-stage retriever index — the dominant cost of the first
  `route()` call on a large catalog — keyed by a deterministic corpus
  fingerprint. The cache has an in-process LRU layer (reuse across `Router`
  instances in one process, folding in the cross-call reuse of #543) and an
  optional on-disk layer (`RoutingIndexCache(directory=...)`, deterministic JSON
  written atomically) that survives process restarts (#624). Opt in via
  `Router(graph, items=items, retriever=CachedRetriever(TfIdfRetriever(),
  cache))`; warm loads are **byte-identical** to a cold fit, so routing quality
  and determinism are unchanged. The cache never raises into the routing path —
  a corrupt or version-incompatible payload is treated as a miss and re-fitted.
  Added `benchmarks/routing_scale.py` + `make benchmark-routing-scale`
  (non-gating) which profiles routing up to 10k tools and writes the bottleneck
  report at `docs/benchmarks/routing-scale.md` (#684), and a routing-quality
  guardrail suite (`tests/test_routing_quality_guardrails.py`) pinning the
  recall floor and cache transparency over the gold set (#686). No new
  dependency. See `docs/benchmarks/routing-scale.md`.

- **HTTP sidecar: language-agnostic route/compact API (#427, #674/#675/#676/#677/#678).**
  New `contextweaver serve-api` exposes the deterministic router and the context
  firewall over a small, versioned HTTP/JSON API so non-Python agents can use
  them without embedding Python: `POST /v1/route` (tool routing), `POST
  /v1/compact` (tool-result compaction), and an unauthenticated `GET /v1/health`
  liveness probe. Built on the Python standard library (`http.server`) with **no
  new dependency**, reusing the same sync `Router` and `compact_tool_result`
  facade as the in-process API. New public surface in `contextweaver.adapters`:
  `SidecarApp` + `SidecarConfig` (transport-free dispatch with optional
  bearer-token auth, per-client rate limiting, body-size cap, and typed
  `SidecarError` responses), `serve_api` / `make_sidecar_server`, and the
  `RouteRequest` / `RouteResponse` / `CompactRequest` / `CompactResponse`
  contract dataclasses (`SIDECAR_API_VERSION`). Versioned JSON Schemas + example
  payloads ship under `schemas/sidecar/v1/`; clients in
  `examples/sidecar_demo.py` (Python, also runs under `make example`) and
  `examples/sidecar/client.ts` (TypeScript, dependency-free). A non-gating
  `make sidecar-smoke` CI step drives the transport in-process. See
  `docs/sidecar.md`.

- **`contextweaver verify` subcommand (#657).** New non-gateway verification
  mode giving library-first adopters a fast, deterministic, network-free
  smoke test of core functionality.  Checks import path, `ContextManager`
  instantiation, a minimal context build, token counting, and routing.  Outputs
  a Rich table for humans (`--json` for CI/automation) with a clear pass/fail
  exit code and actionable fix hints.  Documented in `docs/quickstart.md`.

- **Puppetmaster integration pattern (#416).** New `docs/integration_puppetmaster.md`
  shows how contextweaver consumes Puppetmaster-style job artifacts, worker
  summaries, logs, and follow-up reads without dumping raw artifacts into model
  context. Covers artifact summary ingestion, drilldown via handles/selectors,
  route/answer phase budgeting over job history, and explicit boundaries (in:
  context consumer; out: job supervisor / worker orchestrator).

- **Gateway resource & prompt runtime (#669 / #670).** New
  `PrimitiveGatewayRuntime` (+ the `PrimitiveUpstream` protocol) extends the
  gateway's bounded-choice routing and context-firewall treatment from tools to
  MCP **resources** and **prompts** (#555). Resources/prompts are modelled as
  `SelectableItem`s (`kind="resource"` / `"prompt"`) so they reuse the routing
  `Catalog` / `Router` / `ChoiceCard` machinery; each kind routes in its own
  index while sharing one `ContextManager` (artifact store + firewall +
  `tool_view`) with the tool runtime. New converters
  `mcp_resource_to_selectable` / `mcp_prompt_to_selectable` and read/get
  envelope wrappers live in `contextweaver.adapters.mcp_primitives`; declared
  prompt arguments become an `args_schema` so `prompt_get` validates inputs like
  `tool_execute`. The `SelectableItem` / `ChoiceCard` `kind` set now includes
  `resource` and `prompt`. Four new gateway meta-tools — `resource_browse` /
  `resource_read` / `prompt_browse` / `prompt_get`
  (`contextweaver.adapters.mcp_gateway_primitives`) — expose the bounded-choice
  surface, and `McpGatewayServer` advertises and dispatches them over stdio when
  constructed with a `primitive_runtime=`.
- **Unified cross-primitive identity & collision policy (#671).** New
  `contextweaver.routing.primitive_id` is the single source of truth for
  identifying MCP tools, resources, and prompts in one shared `Catalog`
  (groundwork for routing resources/prompts through the gateway, #555). Tools
  keep their bare canonical `tool_id`; resources and prompts get
  disjoint-by-construction ids via a reserved `kind::` prefix
  (`resource::fs:readme#ab12cd34`, `prompt::gh:summarize#deadbeef`). Stable
  per-kind shape hashes (`compute_resource_hash8` over the URI;
  `compute_prompt_hash8` over name + sorted argument names) and a deterministic
  `~N` collision policy (`resolve_collisions`) round out the surface. Documented
  in `docs/gateway_spec.md` §9.
- **Resources/prompts reachable end-to-end via the gateway (#669 / #670 / #672 /
  #673).** Three concrete `PrimitiveUpstream` adapters now ship in
  `contextweaver.adapters.mcp_primitive_upstream` — `StubPrimitiveUpstream`
  (in-process, for tests/CLI/air-gapped CI), `McpClientPrimitiveUpstream` (wraps
  a connected MCP `ClientSession`), and `MultiplexPrimitiveUpstream` (multi-server
  fan-out) — mirroring the tool `mcp_upstream` trio; per the protocol contract
  they raise transport errors for the runtime to classify. `contextweaver mcp
  serve --gateway` now exposes the four resource/prompt meta-tools when the
  catalog is a snapshot object declaring `resources` / `prompts` alongside
  `tools` (tools-only catalogs stay unchanged), sharing the tool runtime's
  `ContextManager`. `PrimitiveGatewayRuntime` gains `resource_ids()` /
  `prompt_ids()` accessors mirroring `ProxyRuntime.list_tool_ids()`. The
  mixed-primitive context-shaping benchmark is runnable via `make
  benchmark-primitives`, and `docs/gateway_spec.md` §9.4–§9.5 document the
  request flows and the serve/catalog wiring. Malformed snapshot-catalog
  primitive entries (non-dict, or missing the required `uri` / `name` identity
  field) are now skipped with a warning instead of being silently dropped, so a
  mistyped resource/prompt entry surfaces in the serve logs.
- **Stable error codes + remediation hints (#635).** Every
  `ContextWeaverError` subclass now carries a frozen, machine-readable `code`
  (e.g. `CW_CONFIG`) so programs can branch on failures without string-matching,
  plus an optional `hint` (with a class-level `default_hint` fallback). `str(exc)`
  renders `[code] message (hint: …)`, so CLI error output surfaces both
  automatically. Codes are golden-listed in `tests/test_exceptions.py` (a rename
  or a code-less new exception fails CI).
- **Error reference page (#637).** New `docs/errors.md` documents every
  exception — stable code, raising modules, common causes, and the fix — with a
  code index table; added to the mkdocs nav, cross-linked from the
  troubleshooting guide, and included in `llms.txt` / `llms-full.txt`.
- **Runtime deprecation machinery (#517).** New internal
  `contextweaver._deprecation` module — `warn_deprecated(...)`, a `@deprecated`
  decorator, and a single registry surfaced via `active_deprecations()` — emits
  `DeprecationWarning`s with consistent, actionable wording ("deprecated since
  X, removal in Y, use Z instead"). Every message starts with
  `contextweaver deprecation:` so CI can escalate the project's *own*
  deprecations to errors (new `filterwarnings` entry in `pyproject.toml`)
  without touching third-party warnings. Documented in `docs/stability.md` and
  the new Upgrading page, with a "Deprecating an API" workflow in
  `docs/agent-context/workflows.md`.
- **Upgrade guide (#616).** New `docs/upgrading.md` states the 0.x versioning
  and deprecation policy and carries the live inventory of active deprecations
  plus per-release "action required" notes.

### Changed

- **Contributor workflow & build-tooling hardening (#705, #706, #709, #710,
  #711, #712).**
  - `Makefile` targets now invoke `$(PYTHON)` (default `python3`, overridable
    via `make <target> PYTHON=...`) so the documented commands run on
    environments that ship only `python3` (#712).
  - New `make floor-deps` and `make tool-smoke` targets (bundled as
    `make ci-full`) reproduce locally the two gating CI jobs `make ci` cannot
    mirror — lowest-direct dependency-floor resolution and the wheel /
    entry-point smoke; only the macOS `tool-run-smoke` cell stays CI-only (#710).
  - A `.gitattributes` marks `CHANGELOG.md`, `llms.txt`, and `llms-full.txt`
    as `merge=union` so concurrent PRs stop hand-resolving conflicts in these
    append-only / generated files; the `drift-check` gate still verifies the
    committed output on `main` (#709).
  - `docs/agent-context/labels.md` is rewritten to match the live label
    taxonomy (`priority:`, `complexity:`, `area/`, `type:` families), and the
    `docs_improvement` / `integration_request` issue templates now apply the
    canonical `area/docs` / `integrations` labels instead of the stale
    colon-prefixed forms (#711).
  - `contextweaver verify` pins `heuristic_counter()` in its manager and build
    checks, so the network-free guarantee no longer depends on
    `ContextManager`'s default estimator (#705); the CLI failure path
    (non-zero exit + fix-hint rendering) is now covered by tests (#706).

### Deprecated

- **Pre-1.0 legacy compatibility shims (#642).** The following now emit a
  `DeprecationWarning` (behavior unchanged; nothing removed yet — see the
  Upgrading inventory for replacements and the 1.0 removal milestone):
  - `RouteResult.debug_trace` → use `RouteResult.trace`.
  - `RouteTrace.to_legacy_dicts()` → use the structured `RouteTrace` fields.
  - the `Router(scorer=...)` constructor argument → use `retriever=` or
    `scorer_backend=`.

  The `contextweaver.ToolCard` / `contextweaver.types.ToolCard` alias (→ use
  `SelectableItem`), `ChoiceGraph.build_meta`, and the pre-#190 `ArtifactRef`
  write path are recorded as documentation-only deprecations in the upgrade
  guide. `ToolCard` stays a plain alias because the only modules it could warn
  from (pure-data `types.py`, re-export-only `__init__.py`) are barred from
  side effects by hard invariants; the others remain on internal serialization
  paths.

### Fixed

- **Binary MCP resource reads are no longer corrupted (#671 review).**
  `mcp_resource_read_to_envelope` now base64-decodes a resource part's `blob`
  back to its original bytes before persisting it, instead of storing the
  base64 text bytes — so `tool_view` drilldown on real binary resources stays
  byte-accurate. Malformed (non-base64) blobs fall back to their raw bytes.
- **`*_browse` rejects invalid `top_k` cleanly (#671 review).**
  `PrimitiveIndex.browse` now validates `top_k` and returns a structured
  `GatewayError(ARGS_INVALID)` for non-integer or non-positive values, instead
  of letting a bad type reach `make_choice_cards` and raise `TypeError` across
  the meta-tool boundary.
- **Clarified collision-policy determinism & `~N` id status (#671 review).**
  `resolve_collisions` docs and `docs/gateway_spec.md` §9 now state the
  assignment is deterministic *for a given catalog order* (index-based, not
  order-independent), and that the `~N`-suffixed form is an opaque catalog key
  outside the §1.1 grammar (it does not round-trip through `parse_tool_id`).
  Collision tests now use canonical 8-hex-char ids.

## [0.15.0] - 2026-06-14

### Added

- **Structured route→select contract and shortlist composition controls
  (#515, #479, #516, #509).** A focused hardening of the boundary where a
  model picks a tool from a routed shortlist:
  - **Constrained-selection schemas (#515).** `RouteResult.selection_schema(...)`
    (and `contextweaver.selection_schema`) renders the routed candidate IDs as a
    JSON-Schema `enum`, with `json_schema` / `openai` / `anthropic` provider
    variants, so a model can be forced to pick only a routed `tool_id` at
    generation time.
  - **Validated selection contract (#479).** `RouteResult.validate_selection(...)`
    (and `contextweaver.validate_selection`) returns a typed `SelectionValidation`
    (`accepted` / `repaired` / `rejected`) for a returned ID, with deterministic
    repair (whitespace → case-fold → unique prefix; ambiguous matches are
    rejected, never guessed). `RouteResult.to_routing_decision` now validates the
    selection, stores the resolved canonical ID, and records the outcome under
    `metadata["contextweaver"]["selection"]`.
  - **First-class, capping-immune safety field (#516).** `ChoiceCard` gains a
    `safety` field (`""` / `"read_only"` / `"destructive"`) derived from the
    item's safety tags, and the §2.1 five-tag cap now reserves `destructive` /
    `read-only` tags first so a safety marker can no longer be alphabetically
    evicted from the model-facing surface.
  - **Shortlist composition controls (#509).** `Router.route(...)` accepts
    `pin_ids` (always-include items that occupy the first slots regardless of
    relevance) and `namespace_quota` (a per-namespace cap on non-pinned items),
    via `routing.filters.compose_shortlist`. Unset, composition is byte-identical
    to the previous `top_k` truncation.

- **Source-to-catalog adapters: OpenAPI, Agent Skills, and Microsoft Agent
  Framework (#546, #545, #430).** Three new adapters built on the shared
  conversion toolkit (`adapters/_framework_common.py`), extending routing to
  capability sources beyond agent-framework tools:
  - **OpenAPI adapter (#546).** `adapters.openapi` converts an OpenAPI 3.0/3.1
    document (dict, JSON, or YAML) into a `SelectableItem` catalog — one item
    per operation (`openapi_operation_to_selectable`, `openapi_spec_to_catalog`,
    `load_openapi_catalog`). `parameters` + `requestBody` compose into a single
    `args_schema`; local `$ref`s resolve (external refs raise); HTTP methods map
    to read-only / destructive safety tags mirroring the MCP adapter.
    contextweaver routes — it never makes the HTTP call. No extra required.
  - **Agent Skills adapter (#545).** `adapters.agent_skills` loads `SKILL.md`
    skill directories into the catalog as `kind="skill"` items using only their
    frontmatter (`skill_to_selectable`, `load_skills_catalog`); `SkillBodySource`
    hydrates the full Markdown body and bundled resources lazily on selection,
    mirroring `routing.hydration.SchemaSource`. No extra required.
  - **Microsoft Agent Framework adapter (#430).** `adapters.agent_framework`
    converts `AIFunction` tools to a catalog and thread `ChatMessage`s to
    `ContextItem`s with function-call → result parentage
    (`agent_framework_tools_to_catalog`, `from_agent_framework_thread`);
    `[agent-framework]` extra for live loading.
- **Framework adapter expansion + shared conversion toolkit (#454, #502, #501,
  #547, #401).** A coherent pass over the `adapters/` tool-catalog layer:
  - **Shared conversion toolkit (#454).** New private
    `adapters/_framework_common.py` centralises the mechanics the framework
    adapters previously each re-implemented — `infer_namespace`,
    `strip_namespace_prefix`, `coerce_schema_dict`, `collect_tags`,
    `require_name_description`. The CrewAI, Agno, smolagents, Pydantic AI, and
    ChainWeaver adapters now delegate to it with byte-identical behavior, so a
    convention change is one edit instead of up to five.
  - **LangChain adapter (#502).** `adapters.langchain` converts `BaseTool`
    instances (or the plain-dict shape) into a `SelectableItem` catalog
    (`langchain_tool_to_selectable`, `langchain_tools_to_catalog`,
    `load_langchain_catalog`); `[langchain]` extra for live loading.
  - **OpenAI Agents SDK adapter (#501).** `adapters.openai_agents` converts
    function tools to a catalog and run items to `ContextItem`s with
    tool-call → tool-output parentage (`openai_agents_tools_to_catalog`,
    `from_openai_agents_run`); `[openai-agents]` extra.
  - **Google ADK adapter (#547).** `adapters.google_adk` converts ADK tools to
    a catalog and `Session.events` to `ContextItem`s with `function_call` →
    `function_response` parentage (`google_adk_tools_to_catalog`,
    `from_google_adk_session`); `[google-adk]` extra.
  - **Integration table honesty (#401).** The README Framework Integrations
    tables gain a **Code adapter** column distinguishing frameworks with an
    importable adapter (and its extra) from guide-only entries.
- **Context-engine tuning knobs: rendering, kinds, scoring, and overflow
  (#410, #411, #487, #510).** A coherent pass over the context build pipeline's
  selection / scoring / rendering / budget surface, all opt-in with
  byte-identical defaults:
  - **Caller-owned rendering (#410).** `ContextManager.build(...)` /
    `build_sync(...)` accept a `renderer: Callable[[list[ContextItem]], str]`
    hook. When supplied, the caller owns the entire prompt layout — the section
    renderer, header, footer, and episodic/fact assembly are skipped — while
    budget-aware selection and `pack.stats` still run. A ready-made
    `contextweaver.context.passthrough_renderer` joins items by raw text.
  - **Retrieval/RAG kind + presentation override (#411).** New
    `ItemKind.retrieved_doc` gives retrieved/RAG payloads a first-class home
    distinct from authored `doc_snippet`s. A per-item `metadata["section"]`
    override decouples a prompt section label from the filtering `kind`, so
    presentation can change without changing per-phase filtering.
  - **Phase-aware scoring weights + kind priority (#487).** `ScoringConfig`
    gains `kind_priority` (override the built-in item-kind priority table,
    validated to `[0, 1]`) and `phase_overrides` (per-`Phase` weight configs;
    resolution: phase override → base config → built-ins, resolved one level
    deep — a per-phase override that itself defines `phase_overrides` is
    rejected with `ConfigError`). `explain=True`
    surfaces the resolved weights via `ContextBuildExplanation.resolved_weights`.
  - **Budget-overflow policy (#510).** `ContextPolicy.overflow_action`
    (`"drop"` default / `"warn"` / `"raise"`) plus an optional
    `overflow_raise_kinds` scope turn silent budget drops into a logged warning
    or a `BudgetOverflowError` (carrying the would-be `BuildStats`), so a
    dropped mandatory item surfaces as a debuggable error instead of bad output.
- **CI gate consolidation and expansion (#522, #518, #456, #474, #526, #539).**
  A coherent pass over the repo's generated-artifact / convention gating
  infrastructure:
  - **Unified drift harness (#522).** A shared golden-file helper
    (`scripts/_golden.py`) now backs every generated-artifact check, and a
    single `make drift-check` / `scripts/drift_check.py` registry runs them all
    (schemas, scorecards, recorded demos, `llms.txt`, the context-rot SVG, and
    the new public-API manifest). Adding the next generated artifact costs one
    registry entry instead of a fresh copy of the render/compare/exit logic.
    Every registered generator returns a uniform exit code on a missing input
    (the gateway-scorecard generator no longer raises `SystemExit`), so the
    harness aggregates a missing artifact consistently instead of aborting the
    whole run.
  - **Public-API manifest (#518).** `api/public_api.txt` is a committed,
    signature-level snapshot of the public surface, regenerated by `make api`
    and gated by `make api-check` (inside `make drift-check`), so every public
    API addition, removal, or signature change is an explicit, reviewable diff.
  - **Module-size gate (#456).** `make module-size-check` mechanically enforces
    the documented ≤300-line convention: new non-exempt modules must stay under
    the limit, and pre-existing oversized modules are frozen at a grandfathered
    baseline (`scripts/module_size_baseline.json`) that may shrink but not grow.
  - **Doc-snippet execution (#526).** `make doc-snippets-check` extracts and
    runs the Python blocks in `README.md` and a curated docs allowlist, so the
    first code an adopter copies is guaranteed to run against the current API.
    Illustrative blocks opt out with a `<!-- snippet: skip -->` marker.
  - **`examples/` + `scripts/` type-checked (#539).** `make type` now runs
    `mypy src/ examples/ scripts/`, extending strict typing to the most-copied
    code and the gating CI scripts.
  - **`make ci` ⇄ CI alignment + workflow hygiene (#474).** `make ci` now runs
    the consolidated drift gate, module-size, doc-snippet, and README-version
  checks, so a local pass mirrors the gating CI checks. CI gains workflow
  `timeout-minutes`, a PR `concurrency` group, and a docs-build job that gates
  `mkdocs build` on PRs (network-only `weaver-conformance` stays CI-only).
- **README roadmap drift guard (#531).** The README now single-sources the
  framework integration table, marks the current roadmap row with the package
  version, and extends `readme-version-check` so stale roadmap `current` markers
  fail CI instead of drifting silently.
- **Gateway `tool_execute` dispatch hardening (#529, #512, #483, #482, #507).**
  The gateway/proxy dispatch path gains four opt-in, deterministic controls,
  all inert by default so an unconfigured runtime behaves exactly as before:
  - **Retry/backoff (#529).** `ProxyRuntime(retry_policy=RetryPolicy(...))` retries
    transient upstream failures (timeouts, connection errors) with bounded
    exponential backoff + optional jitter. Tool-level error *results* and
    non-retryable codes are never retried; the injected `retry_sleep` keeps the
    schedule testable.
  - **Read-only response cache (#512).** `ProxyRuntime(result_cache=ToolResultCache(...))`
    memoises identical `tool_execute` calls for tools the upstream marks
    read-only (operator opt-in via an optional allow-list). TTL- and size-bounded
    (LRU), argument-order-insensitive keys, errors never cached, invalidated on
    catalog refresh. Read-only eligibility derives from the **unverified**
    upstream `readOnlyHint`, so the docstring and `gateway_spec.md` §4.5 now warn
    that `read_only: true` without an `allow` list trusts each upstream's
    self-declaration and recommend pairing it with an `allow` list for
    safety-critical tools.
  - **Dry run (#483).** `tool_execute(..., dry_run=true)` runs hydration,
    validation, and quota checks then returns a `DryRunReport` (resolved
    `tool_id`, upstream name, validation outcome, unverified annotations, check
    list) **without** invoking upstream or writing artifacts. Invalid args still
    return `ARGS_INVALID`; dry runs never consume quota.
  - **Rate limiting / quotas (#482).** `ProxyRuntime(rate_limiter=RateLimiter(...))`
    enforces per-session and per-minute invocation limits per meta-tool and per
    `tool_id`, returning a structured `RATE_LIMITED` error (with `retry_after`)
    on breach without dispatching upstream.
  - **Catalog-refresh consistency (#507).** Documented and regression-tested that
    `refresh_catalog` rebuilds all catalog-derived state (name index, validators,
    cache, graph) within one synchronous call, so a renamed/removed tool's stale
    `tool_id` yields a clean `HYDRATE_FAILED` — never a dispatch to the wrong
    upstream tool — and cross-upstream duplicate raw names collapse to the first.

    All four controls are loadable from `mcp serve --config` via the `retry`,
    `rate_limits`, and `cache` blocks (validated at startup). New public symbols:
    `RetryPolicy`, `RateLimit`, `RateLimitPolicy`, `RateLimiter`, `ToolResultCache`,
    `DryRunReport`, `call_with_retry` (in `contextweaver.adapters`). See
    `docs/gateway_spec.md` §4.5–§4.6.
- **Persistent gateway sessions: `mcp serve --state-dir` (#511).**
  `contextweaver mcp serve` accepts `--state-dir DIR` (and a `state_dir` config
  key) to wire the gateway's `ContextManager` with file-backed stores —
  `{DIR}/events.sqlite3` (`SqliteEventLog`) and `{DIR}/artifacts/`
  (`JsonFileArtifactStore`). Restarting against the same directory rehydrates
  prior event history and keeps previously issued artifact handles resolvable
  via `tool_view`; an unwritable directory fails fast with a clear startup
  error. Without the flag the gateway keeps its zero-config in-memory behaviour.
  Fixes a latent store-resolution bug where an *empty* persistent backend
  (which is falsy because it defines `__len__`) was silently replaced by an
  in-memory default; `ContextManager` now resolves stores with explicit
  `is None` checks.
- **Remote store backends: Redis & S3 (#426).** New `RedisEventLog` and
  `RedisArtifactStore` (behind `pip install 'contextweaver[redis]'`) and
  `S3ArtifactStore` (`contextweaver[s3]`, works with AWS S3 / MinIO / R2 / GCS
  interop) give multi-process and long-lived gateways durable event/artifact
  storage beyond one process or disk. All three import their client library
  lazily — importing `contextweaver.store` never requires the extra — and are
  run through the #520 conformance kit (against `fakeredis` and `moto` in CI,
  no service container required). `RedisArtifactStore` supports an optional
  per-artifact TTL and namespace isolation; `S3ArtifactStore` supports a key
  prefix and a custom `endpoint_url`.
- **Stdlib SQLite episodic & fact stores (#496).** New `SqliteEpisodicStore`
  and `SqliteFactStore` (`contextweaver.store`) give long-lived agents durable
  episodic/fact memory with zero external services, built on the same
  `_sqlite_base` scaffolding as `SqliteEventLog`. They are schema-versioned,
  re-instantiable against an existing file, and can share one database file
  with the event log (each store type tracks its own migrations under a
  distinct version table). `SqliteEpisodicStore.search` delegates ranking to a
  transient in-memory store, and `SqliteFactStore` keeps `fact_id` ordering, so
  swapping either backend for its in-memory counterpart leaves context-build
  output byte-identical. (`apply_migrations` / `schema_version` gained an
  optional `version_table` argument to support the shared-file layout.)
- **Async store protocol variants (#495).** New `AsyncEventLog`,
  `AsyncArtifactStore`, `AsyncEpisodicStore`, and `AsyncFactStore` protocols
  (`contextweaver.store.async_protocols`) mirror the sync surface so
  network-backed backends can avoid blocking the async-first context pipeline.
  `to_async(store)` wraps any sync backend as the matching async protocol via
  `asyncio.to_thread` (each bridge serializes concurrent awaits on itself with a
  per-bridge lock, since the in-memory backends are not thread-safe);
  `to_sync(async_store, loop)` does the inverse. `ContextManager` now accepts
  async store backends (via `StoreBundle`) and keeps the event loop responsive
  during `await build(...)` and `await build_call_prompt(...)` by offloading the
  synchronous pipeline body to a worker thread while the async store I/O runs on
  a private loop thread; the loop thread is released automatically when the
  manager is garbage-collected (via `weakref.finalize`, so no new public
  `close()` method is added to `ContextManager`). Concurrent build calls on one
  manager serialize on an internal lock so the offloaded pipeline runs never
  race on the thread-unsafe in-memory stores.
  Async conformance checks (`check_async_*_conformance`) ship in
  `contextweaver.store.testing`. (Thread-affine backends such as `SqliteEventLog`
  are not valid `to_async` targets; their async story is a future native
  `aiosqlite` backend.)
- **Store-protocol conformance kit (#520).** New framework-agnostic
  `contextweaver.store.testing` module — `check_event_log_conformance`,
  `check_artifact_store_conformance`, `check_episodic_store_conformance`,
  `check_fact_store_conformance` — each takes a factory for an empty backend
  and asserts the round-trip, ordering, and not-found contract the Context
  Engine relies on — including that `ArtifactStore.put()` stamps a sha256
  `content_hash` on the returned ref, now documented as a protocol contract
  because the firewall's idempotency short-circuit (#190) depends on it. It
  imports no test framework, so it ships in the core wheel and runs under
  pytest, `unittest`, or a plain script. The bundled in-memory, JSON-file,
  and SQLite backends are all run through it.
- **`JsonFileArtifactStore` durability hardening (#497).** Writes are now
  **atomic** (temp file + `os.replace`), so a crash mid-write never leaves a
  truncated artifact; `list_refs()` reads an in-memory handle→ref index built
  once on construction instead of rescanning the directory on every call (only
  self-consistent metadata+data pairs are indexed, so the index never lists a
  handle `get()` cannot serve); and optional `max_bytes` / `max_artifacts`
  constructor limits bound disk growth, raising the new `ArtifactStoreQuotaError`
  when a write would breach them. `put` / `delete` / `list_refs` are serialised
  by an internal lock, making a single instance safe to share across threads in
  one process.
- **`ArtifactStoreQuotaError`** exception (subclass of `ContextWeaverError`),
  exported from the package root.
- **Documented store thread-safety contract (#458)** in
  `docs/agent-context/architecture.md`, with concurrency tests covering atomic
  overwrites, concurrent distinct-handle reads/writes, and concurrent gateway
  `tool_view` drilldown.

### Changed

- **Artifact stores now persist a `content_hash` (#466).** Both
  `InMemoryArtifactStore.put` and `JsonFileArtifactStore.put` compute and store
  the sha256 of the content on the returned `ArtifactRef`. This makes the
  firewall's re-processing idempotency short-circuit (#190) survive a process
  restart when the ref is reloaded from disk. The firewall no longer recomputes
  the hash separately.
- **`JsonFileArtifactStore` percent-encodes handles into filenames (#466).**
  Handles containing characters that are legal in a handle but hostile in a
  filename — notably `:` (the firewall's `artifact:result:…` shape, which
  opens an NTFS alternate data stream on Windows) — are now stored portably.
  On-disk filenames change accordingly (`handle.data` → `enc(handle).data`).
- **`InMemoryArtifactStore.to_dict`/`from_dict` round-trip is now lossless
  (#466).** Raw bytes are serialised (base64) alongside the metadata index, so
  a restored store resolves `get()`/`drilldown()` instead of returning refs
  whose handles dereference to nothing — this is what lets a `StoreBundle`
  carry firewalled artifacts across a restart.
- **The gateway no longer assumes a concrete artifact-store backend (#472).**
  `drilldown` is part of the `ArtifactStore` protocol, so `ProxyRuntime.view`
  (`tool_view`) dropped its `cast`/`type: ignore` to `InMemoryArtifactStore`
  and works against any conformant store (e.g. `JsonFileArtifactStore`).

- **Opt-in deterministic secret-redaction pass (#428).** A new pure
  `contextweaver.secrets` module (`scrub_secrets()`, `contains_secret()`,
  `SecretPattern`) detects well-known secret shapes (cloud access keys, provider
  tokens, private-key blocks, JWTs, credential-bearing URLs, `key=value`
  credential assignments). `ContextManager(redact_secrets=True)` scrubs firewall
  summaries and extracted facts before they reach the prompt; `ProxyRuntime(...,
  redact_secrets=True)` additionally scrubs `ChoiceCard` text. A `SecretRedactor`
  `RedactionHook` (registered as `"secret"`) is available for
  `ContextPolicy.redaction_hooks`. Off by default; only ever tightens a surface.
- **Opt-in ingestion-time sensitivity classification (#542).** New
  `SensitivityClassifier` protocol + built-in `HeuristicSensitivityClassifier`
  (and `detect_sensitivity()`) raise an item's sensitivity label before
  enforcement so content callers forgot to label (e.g. tool results carrying
  credentials/PII) no longer defaults silently to `public`. Wired via
  `ContextManager(sensitivity_classifier=...)`; runs at the start of the
  sensitivity stage and over fact/episode header content. A classifier may only
  raise a label, never lower it. Every raise records
  `metadata["sensitivity_raised_by"]` (the classifier's type name) so the
  decision is auditable.
- **Gateway untrusted-input hardening (#464, #484, #485, #488).** The
  proxy/gateway ingest, validation, and dispatch boundary now defends against
  malformed or hostile upstream input:
  - **Defensive tool-def registration (#464).** A malformed upstream tool
    definition (non-dict, or missing a non-empty string `name`) no longer
    aborts catalog refresh; the offending tool is skipped and recorded on the
    new `ProxyRuntime.last_refresh_report` (`CatalogRefreshReport`). A new
    `on_invalid="raise"` mode fails loudly for development catalogs.
  - **Untrusted-schema validation + validator caching (#484).** Upstream
    `inputSchema`/`outputSchema` are meta-validated (`check_schema`) and bounded
    for serialized size, nesting depth, and property count (configurable via
    `SchemaLimits`) at ingest, surfacing `SchemaFinding`s on the refresh report.
    Compiled `jsonschema` validators are cached per `tool_id`, removing
    per-call recompilation from the hot `tool_execute` path. A malformed schema
    surfaces as the new `SCHEMA_INVALID` error code.
  - **Structured upstream-error taxonomy (#485).** `GatewayError` gains a
    `retryable` hint and the codes `UPSTREAM_TIMEOUT`, `UPSTREAM_UNAVAILABLE`,
    `AUTH_FAILED`, `PERMISSION_DENIED`, and `RATE_LIMITED`
    (`classify_upstream_exception`), with `UPSTREAM_ERROR` kept as the fallback.
    Model-visible upstream detail is now control-character-stripped and
    length-capped (`redact_upstream_detail`); operators keep full detail via
    logging.
  - **Opt-in tolerant argument normalization (#488).**
    `ProxyRuntime(tolerant_args=True)` runs a deterministic, rule-based repair
    pass (`normalize_args`) before strict validation — stringified JSON objects
    and string→`int`/`number`/`boolean`/`null` coercions, only when the schema
    type demands it. Off by default (byte-identical behaviour); every repair is
    recorded under the result envelope's `provenance["arg_repairs"]`.

### Changed

- **Header memory is now enforced (#450).** Facts (`add_fact`) and episode
  summaries (`add_episode`) injected into the prompt header are routed through
  the sensitivity floor/redaction action **and** the per-phase `memory_fact`
  kind policy — closing a side-channel where header content bypassed stage-3
  enforcement. `Fact` and `Episode` gained an optional `sensitivity` field
  (defaults `public`, round-trips in `to_dict`/`from_dict`); `add_fact` /
  `add_episode` accept a keyword-only `sensitivity`. A phase that excludes
  `memory_fact` no longer receives fact/episode text via the header.
- **Redaction is effective end-to-end (#451).** A redacted item now drops its
  `artifact_ref` and is stamped `metadata["redacted"]=True`, so the rendered
  prompt no longer advertises an artifact handle that `drilldown` could
  dereference back to the original, pre-redaction bytes. `drilldown` is now also
  policy-aware: a drilldown whose source item meets the sensitivity floor (or was
  redacted) raises `PolicyViolationError` unless the new
  `ContextPolicy.allow_redacted_drilldown=True` opt-out (default `False`, closed)
  is set, and an injected drilldown slice inherits its source item's sensitivity
  instead of defaulting to `public` — so filtered content cannot be laundered back
  in via the drilldown path.
- **`deterministic=True` now also gates LLM-backed extractors (#461).** The
  firewall's fail-closed determinism guarantee previously covered only the
  summarizer; an LLM-backed `Extractor` (e.g. `LlmExtractor`) would still run.
  Both the large-output firewall path and the small-output ingest path now raise
  `DeterminismError` rather than passing data through a model.

- **`contextweaver catalog lint` (#538).** A new `catalog` CLI sub-app exposes
  `catalog lint FILE`, which runs the existing `CatalogNormalizer` plus
  cross-item reference validation over a catalog and reports findings (missing
  descriptions, duplicate/blank IDs, tag/whitespace hygiene, dangling
  `depends_on`/`requires`). Accepts the native JSON/YAML catalog, a raw MCP
  `tools/list` array, and the `{"tools": [...]}` snapshot shape. Supports
  `--json` and exits `0` (clean) / `1` (findings) / `3` (load error) for CI
  gating; never mutates the input file.
- **Typed cross-item reference validation on catalog load (#519).** New
  `routing.validate_references()` + `Catalog.validate_references()` return a
  `CatalogValidationReport` of dangling `depends_on` (item IDs) and unsatisfied
  `requires` (capabilities) references. The `load_catalog*` loaders gained an
  additive `on_invalid` kwarg (`"warn"` default → log per finding, `"raise"` →
  `CatalogValidationError` carrying the report, `"ignore"`). Per-item
  deserialization failures now name the offending item by `id` (or index).
- **Structured DEBUG/INFO routing diagnostics (#524).** `logging.DEBUG` on
  `contextweaver.routing` now traces the previously silent decision points:
  the tree-building strategy per subtree (INFO when a clustering/alphabetical
  *fallback* is taken), per-step beam pruning counts and pruned IDs, and the
  original-vs-augmented scoring query. Log messages are diagnostics, not API.
- **Script-aware offline token heuristic — `HeuristicEstimator` (#525).** The
  default estimator (and the `tiktoken` offline fallback) now counts dense
  scripts (CJK, Kana, Hangul, emoji) at ≈1 token/character instead of
  `len // 4`, fixing a ~4× budget under-count on non-Latin content. Latin/ASCII
  estimates are unchanged. Dependency-free (stdlib range checks); exposed via
  `tokens.heuristic_counter()` and `contextweaver.HeuristicEstimator`.
- **Provider-calibrated token estimation (#493).** Register accurate counters by
  name (`tokens.register_estimator(name, counter)`) and select them via
  `tokens.get_token_counter(provider)`; `tiktoken` stays the default. The
  estimator path that produced a build's numbers is recorded on the new
  additive `BuildStats.token_estimator` field (e.g. `"tiktoken/cl100k_base"`,
  `"heuristic/v2"`, or a registered provider name). New
  `benchmarks/token_calibration.py` (+ `make token-calibration`) renders the
  divergence table at `docs/token_calibration.md` across ≥4 corpus shapes;
  provider `count_tokens` legs are opt-in via `CW_TOKEN_CALIBRATION_PROVIDERS`
  and never run in CI.
- **Non-ASCII regression suite (#525).** `tests/test_unicode_regression.py`
  pins CJK/emoji/RTL behaviour across tokenization, budgeting, dedup, card
  rendering, serialization, and an in-process build.
- **Dockerfile for the MCP gateway.** A top-level `Dockerfile` (+ `.dockerignore`)
  boots `contextweaver mcp serve --gateway` over stdio against the packaged
  reference catalog, so an MCP client or automated scanner (e.g. Glama) can
  build, start, and introspect the gateway with no extra configuration. The
  image build validates the catalog with `--dry-run`.
- **`unregister_redaction_hook(name)` (#463).** Companion to
  `register_redaction_hook` for test hygiene and long-lived processes that need
  to replace a hook; raises `ItemNotFoundError` for an unknown name.
- **`ValidationError` exception (#463).** New
  `contextweaver.exceptions.ValidationError`, raised by the pure-data layer
  (`ChoiceCard` construction, `RoutingDecision.from_dict`). It derives from both
  `ContextWeaverError` and the builtin `ValueError`, so the custom hierarchy is
  catchable while existing `except ValueError` call sites keep working.
- **`compact_tool_result(..., overwrite_sidecar=True)` (#467).** Opt-in escape
  hatch to replace an existing reserved `_cw` sidecar when round-tripping prior
  contextweaver output back through the facade (default refuses — see below).

### Changed

- **Custom view generators now fire on every ingestion/build path (#460).** A
  generator registered on `ContextManager.view_registry` previously only ran on
  `ingest_tool_result`; it now also runs on the build-time firewall batch and
  `ingest_mcp_result`. Users with custom generators will start seeing them fire
  on the previously-unwired paths (the intended behavior); default-registry
  output is unchanged.
- **Collision-proof fact IDs (#462).** `ContextManager.add_fact` now mints IDs
  from a monotonic per-manager counter (`fact:{key}:{seq}`) instead of the
  store's current size. A delete followed by a new `add_fact` can no longer
  re-mint an existing fact's ID and silently overwrite it; IDs stay
  deterministic for a fixed call sequence, and the call no longer scans the
  full store. A pre-populated store that collides with the counter now raises
  `DuplicateItemError` loudly rather than overwriting.
- **Construction-time validation in core data types (#463).**
  `ContextPolicy.sensitivity_action` is now typed `Literal["drop", "redact"]`
  and validated in `__post_init__` (raises `ConfigError` immediately instead of
  at the first build). `ChoiceCard` bounds violations now raise `ValidationError`
  (still a `ValueError` subclass). `register_redaction_hook` raises `ConfigError`
  (was `PolicyViolationError`) on a duplicate name — a configuration mistake, not
  a policy violation.
- **Actionable graph-validation diagnostics (#523).** `GraphBuildError` now
  carries structured `cycle` / `edge` / `missing_root` attributes and names the
  specifics in its message: cycle failures report the full path
  (`a -> b -> c -> a`, deterministically), dangling edges name both ends, and a
  missing root lists known-node hints. The structured attributes are the stable
  contract; message text is not.
- **`Mode.adaptive` no longer fails silently (#521).** Constructing a
  `ProfileConfig(mode=Mode.adaptive)` now emits a `UserWarning` stating the mode
  is inert (no pipeline stage honours it; output equals `Mode.strict`).
  `strict`/`seeded` are unaffected and persisted `"adaptive"` profiles still
  round-trip (re-warning on load).
- **`contextweaver mcp serve` advertises the installed package version.**
  `--version` now defaults to the contextweaver package version (was `None`)
  when neither the flag nor the config file sets it, and the resolved version
  is shown in the serve lifecycle line.

### Fixed

- **`compact_tool_result` honours the reserved `_cw` namespace (#467).** A
  payload that already carries the reserved `_cw` sidecar key now raises
  `ConfigError` instead of being silently clobbered (matching the
  `metadata['_contextweaver']` reserved-namespace rule). Pass
  `overwrite_sidecar=True` to opt into replacing it.
- **`RoutingDecision.from_dict` no longer fabricates timestamps (#463).** A
  missing or unparseable `timestamp` now raises `ValidationError` instead of
  substituting `datetime.now()`, keeping the pure-data layer deterministic and
  its round-trips lossless.
- **Removed load-bearing `assert`s from library code (#467).** Correctness
  checks in `firewall_api.py`, `build.py`, and `_manager_build.py` are now
  explicit raises (`ContextWeaverError`-family) so they are not silently
  stripped under `python -O`. Type-narrowing asserts are retained where
  annotated. Two new guard tests (`tests/test_source_invariants.py`) enforce
  this and the custom-exception rule going forward.

## [0.14.1] - 2026-06-11

### Added

- **MCP Registry listing + PyPI ownership marker (#348).** Adds a
  registry-publishable `server.json` describing the gateway as a
  `uvx contextweaver mcp serve --config <gateway.yaml>` stdio server (linking
  to the gateway quickstart, not the raw API docs), an
  `mcp-name: io.github.dgenio/contextweaver` marker in the README for PyPI
  ownership verification, and a release-triggered GitHub Actions job that
  publishes to the official MCP Registry via GitHub OIDC (no interactive
  login required).
- **Trustworthy diagnostics across context builds and the MCP gateway
  (#370, #378, #398, #414, #459).** `BuildStats.dropped_items` attributes
  every excluded item to `sensitivity`, `dedup`, `kind_limit`, or `budget`;
  the production context pipeline now fires exclusion and budget lifecycle
  hooks. New versioned `DiagnosticEvent` / `DiagnosticSink` APIs include
  thread-safe in-memory and append-only JSONL sinks. `ProxyRuntime` emits
  sanitized catalog, browse, hydrate, execute, and artifact-view events with
  counts, token/schema savings, failures, and latency. Operators can use
  `contextweaver mcp inspect`, `contextweaver mcp stats`, and
  `contextweaver inspect` for JSON or Markdown reports without exposing raw
  queries, argument values, result text, prompt text, or artifact bytes.
- **Single-call firewall facade — `compact_tool_result()` /
  `firewalled_tool_result()` (#399).** Shrink one large tool result before it
  enters the prompt without standing up a `ContextManager`. Returns a
  `CompactResult` (`firewalled`, `payload`, `summary`, `facts`, `artifact_ref`,
  `stats`). Exported from the top level.
- **Structured (lossless) firewall mode (#406).** New `StructuredFirewall(keep=[...])`
  plus `summarize.structured.project` / `parse_path`: keep an allow-list of
  JSON paths inline, offload the rest to the artifact store (retrievable via
  `drilldown`), no LLM. Selectable through `compact_tool_result(strategy=...)`
  and `ContextManager.ingest_tool_result(..., firewall=StructuredFirewall(...))`.
  An explicit `strategy="structured"` now raises `ConfigError` on non-JSON
  input instead of silently downgrading to a text summary; `ingest_tool_result`
  applies `firewall=` only above `firewall_threshold`.
- **First-class firewall diagnostics — `FirewallStats` (#402).** Records
  `triggered`, `strategy`, original/summary chars+tokens (`chars_saved` /
  `tokens_saved`), `artifact_ref`, and `summarized_by_llm`. Surfaced on
  `ResultEnvelope.firewall_stats`, and aggregated on `BuildStats.firewall_events`
  / `BuildStats.firewall_summary()`.
- **Determinism guarantee — `deterministic=True` (#404).** `ContextManager(deterministic=True)`
  and `compact_tool_result(deterministic=...)` *fail closed* with the new
  `DeterminismError` rather than passing data through an LLM-backed summariser;
  `FirewallStats.strategy` / `summarized_by_llm` make the path auditable.
- **Built-in token counter — `contextweaver.tokens` (#405).** Public
  `count()` / `get_token_counter()` / `heuristic_counter()` (and `TokenCounter`
  alias) so callers never wire `tiktoken` directly; firewall/`FirewallStats`
  numbers use the same counter. New no-op `contextweaver[tokenizers]` extra
  documents the contract (`tiktoken` is already core, with offline fallback).
- **Daily Driver guide for MCP gateway operators (#394).** New
  `docs/daily_driver.md` explains when to use or bypass contextweaver,
  copy-paste operating instructions for common MCP clients, and a practical
  debug loop using route explanations, `BuildStats`, artifact views, and OTel.
- **MCP gateway security and data-flow model (#396).** New
  `docs/security_model.md` distinguishes prompt exposure from raw artifact
  storage, documents trust and egress boundaries, and records the current
  `tool_view` / artifact-lifecycle limits tracked by #375.
- **Verified Claude Code MCP recipe (#429).** Adds project/local registration
  commands, a committed `.mcp.json` example, operating instructions, and
  troubleshooting verified against Claude Code 2.1.165.
- **Zero-install CLI smoke coverage (#437).** Linux and macOS CI now build the
  wheel and run its `contextweaver` entry point through isolated `uvx` and
  `pipx` environments.

### Changed

- **Token estimates flow through one source of truth (#530).** The
  sensitivity-redaction placeholder, the firewall summary item, card budgeting
  (`routing/cards.count_tokens`, `routing/packer`), and memory-source costing
  no longer carry inline `len // 4` literals — they route through the
  configured estimator / `contextweaver.tokens`. The sensitivity stage receives
  the manager's estimator, so a custom counter is honoured on redaction paths.
  ASCII placeholder estimates are unchanged; offline non-Latin estimates become
  more accurate (and generally higher), which can shift selection outcomes in
  offline mode by design. The default `ContextManager` estimator is now
  `HeuristicEstimator` (was `CharDivFourEstimator`); `heuristic_counter()`
  returns it.
- **`BuildStats` accounting now has one pipeline owner (#459).**
  `total_candidates` is measured after dependency closure and before
  sensitivity filtering; `dropped_count` includes every later exclusion, so
  completed builds satisfy `included_count + dropped_count ==
  total_candidates`. The report schema is version 2.
- **CI now exercises every committed generated-artifact drift check
  (#389–#393).** `llms.txt` / `llms-full.txt`, recorded demo casts, and the
  gateway scorecard are gating checks on the Python 3.12 matrix cell; the
  deterministic smoke evaluation also runs there as a non-gating signal.
- **MCP client recipes now use the installed CLI (#371, #437).** Claude
  Desktop, Claude Code, GitHub Copilot, and Cursor configs launch
  `uvx contextweaver mcp serve`; docs no longer describe the dedicated CLI as
  future work. `examples/recipes/serve_gateway.py` remains a labelled
  legacy/custom-runtime example, while config tests reject references to that
  launcher across relative, absolute, POSIX, and Windows path forms. Relative
  catalog paths now resolve from the config file, and text results expose their
  stored artifact handle so clients can call `tool_view`.

## [0.14.0] – 2026-06-07

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

- **Decomposed `ContextManager` to meet the ≤300-line module guideline (#101).**
  The pipeline *logic* already lived in `context/build.py`
  (`run_build_pipeline`), `context/route_build.py`, `context/call_prompt.py`,
  and `context/ingest.py`; what remained was the manager's own method surface
  (`manager.py` was 878 lines of thin delegating stubs + docstrings). Those
  stubs now live in flat, single-level *partial-class* mixins —
  `_IngestMixin` (`context/_manager_ingest.py`), `_BuildMixin`
  (`context/_manager_build.py`), `_RoutingMixin` (`context/_manager_routing.py`)
  — sharing a `_ManagerState` base (`context/_manager_base.py`) that declares
  the private-attribute contract. `manager.py` is now **239 lines** (only
  `__init__`, properties, `drilldown`, and mixin composition); every module is
  ≤300. The delegate pipeline functions are now typed against `_ManagerState`
  (interface segregation; `ContextManager` inherits it via the mixins, so every
  call site is unchanged). No public API change — all 21 methods stay on
  `ContextManager` and the full test suite passes unmodified.
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

- **Routing history tool-id resolution narrows its exception handling.**
  `route_build.resolve_tool_id_from_result` previously wrapped the parent
  event-log lookup in a bare `except Exception`, silently swallowing any error
  before falling back to `parent_id`. It now catches only `ItemNotFoundError`
  (the documented `EventLog.get` contract), so unexpected store errors surface
  instead of being hidden (PR #363 review).
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

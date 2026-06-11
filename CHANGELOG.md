# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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

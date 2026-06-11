# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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

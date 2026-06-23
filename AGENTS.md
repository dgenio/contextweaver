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
| `envelope.py` | Result types: `ResultEnvelope`, `BuildStats`, `DroppedItem`, `ContextPack`, `ChoiceCard`, `HydrationResult`, `RoutingDecision` |
| `diagnostics.py` | Versioned, payload-safe gateway events and sinks (`DiagnosticEvent`, `DiagnosticSink`, JSONL/in-memory sinks) plus deterministic aggregate reports (issues #370/#378). |
| `inspection.py` | Pure JSON/Markdown report construction for offline context, routing, and artifact inspection without raw payload content (issue #398). |
| `config.py` | Configuration: `ContextBudget`, `ContextPolicy` (incl. `overflow_action` budget-overflow policy, issue #510), and re-exported `ScoringConfig` |
| `_scoring_config.py` | `ScoringConfig` — candidate-scorer weights, incl. `kind_priority` + per-`Phase` `phase_overrides` (issue #487). Extracted from `config.py` to keep it ≤300 lines; re-exported there so `from contextweaver.config import ScoringConfig` is unchanged. |
| `profiles.py` | Routing and profile config: `Mode`, `RoutingConfig`, `ProfileConfig`, named presets |
| `protocols.py` | Protocol interfaces: `TokenEstimator`, `EventHook`, `Summarizer`, `Extractor`, `RedactionHook`, `SensitivityClassifier` (ingestion-time labelling, issue #542), `MemorySource`, `Labeler`, `Retriever`, `Reranker`, `ClusteringEngine`, `RoutingScoreProvider` (store protocols re-exported from `store/protocols.py`). Bundled estimators: `HeuristicEstimator` (default, script-aware, dependency-free — counts CJK/Kana/Hangul/emoji ≈1 token/char, issue #525), `CharDivFourEstimator` (raw `len // 4` primitive), `TiktokenEstimator` (exact, falls back to `HeuristicEstimator` offline). Each carries a stable `name` for `BuildStats.token_estimator`. |
| `store/protocols.py` | Store-layer protocols: `EventLog`, `ArtifactStore`, `EpisodicStore`, `FactStore` |
| `store/async_protocols.py` | Async counterparts `AsyncEventLog` / `AsyncArtifactStore` / `AsyncEpisodicStore` / `AsyncFactStore` (issue #495) — same surface, `async def`. Consumed only by the async `context/` path; backend-agnostic. |
| `store/async_bridge.py` | `to_async(sync_store)` — wraps a *thread-safe* sync backend as the matching async protocol via `asyncio.to_thread`. Thread-affine backends (`SqliteEventLog`, `check_same_thread=True`) are not valid targets (issue #495). |
| `store/_async_to_sync.py` | Inverse bridges + `to_sync(async_store, loop)` + `is_async_store()` (issue #495). Drives async stores on a private `_LoopThread` so the existing sync pipeline can consume them; `ContextManager` offloads `build` to a worker thread when async-backed. Not public API. |
| `exceptions.py` | Custom exception hierarchy (all errors inherit `ContextWeaverError`). Each class carries a stable, frozen `code` (e.g. `CW_CONFIG`) plus an optional `hint`; `str(exc)` renders `[code] message (hint: …)`. Codes are documented in `docs/errors.md` and golden-listed in `tests/test_exceptions.py` (issues #635, #637). |
| `_utils.py` | Text similarity primitives: `tokenize()`, `jaccard()`, `TfIdfScorer` |
| `secrets.py` | Pure, deterministic secret detection/scrubbing primitives: `scrub_secrets()`, `scrub_secrets_in_list()`, `contains_secret()`, `SecretPattern` (issue #428). Shared by the firewall secret-scrub, the `SecretRedactor` hook, the sensitivity classifier, and ChoiceCard scrubbing. No I/O; never weakens a surface (only removes characters). |
| `_version.py` | Single-source version derived from `importlib.metadata`; fallback `"0.0.0+local"` |
| `_demos.py` | Demo logic for the CLI `demo` subcommand (exempt from `print()` rule) |
| `serde.py` | Serialisation helpers for `to_dict` / `from_dict` |
| `tokens.py` | Built-in token counter (`count()`, `get_token_counter()`, `heuristic_counter()`, `TokenCounter` alias) plus the provider-estimator registry (`register_estimator()`, `registered_estimators()`, `estimator_name()`, issue #493). The **single source of truth** for token counts — firewall, sensitivity-redaction placeholders, card budgeting (`routing/cards.count_tokens`), and `FirewallStats`/`BuildStats` numbers all route through it (issues #405/#493/#530); no stray `len // 4` literals elsewhere. Owns the `tiktoken` dependency; offline it falls back to the script-aware `HeuristicEstimator`. |
| `store/` | In-memory data stores: `EventLog`, `ArtifactStore`, `EpisodicStore`, `FactStore`, `StoreBundle` |
| `store/_sqlite_base.py` | Shared SQLite connection + migration scaffolding (WAL, `foreign_keys=ON`, `_contextweaver_schema_version` table). Reused by every SQLite-backed store (issue #174). |
| `store/sqlite_event_log.py` | `SqliteEventLog` — first persistent `EventLog` backend; single-process, sync, append-only, schema-versioned (issue #223). |
| `store/sqlite_episodic.py` | `SqliteEpisodicStore` — persistent `EpisodicStore` on `_sqlite_base` (issue #496). Append-only, ordered by `ordinal`; `search` delegates to a transient `InMemoryEpisodicStore` for byte-identical ranking. Own version table (`VERSION_TABLE`) so it can share a DB file with the event log / facts. |
| `store/sqlite_facts.py` | `SqliteFactStore` — persistent `FactStore` on `_sqlite_base` (issue #496). `put` upserts on `fact_id`; `get_by_key`/`all` sorted by `fact_id`. Own version table; shareable DB file. |
| `store/redis_artifacts.py` | `RedisArtifactStore` — Redis `ArtifactStore` for multi-process gateways (issue #426). Namespaced keys, optional per-artifact TTL, `list_refs` via `SCAN`. Lazy `redis` import (`[redis]` extra). |
| `store/redis_event_log.py` | `RedisEventLog` — Redis `EventLog` (issue #426). Items in a hash keyed by id + parallel order list; append-only ordering across processes. Lazy `redis` import (`[redis]` extra). |
| `store/s3_artifacts.py` | `S3ArtifactStore` — S3-compatible `ArtifactStore` (issue #426; AWS/MinIO/R2/GCS). `{prefix}/{handle}.data` + `.json` objects. Lazy `boto3` import (`[s3]` extra). |
| `store/json_file_artifacts.py` | `JsonFileArtifactStore` — filesystem `ArtifactStore` backend; `{enc(handle)}.data` + `{enc(handle)}.json` per artifact, re-instantiable against an existing directory (issue #42). Hardened (issue #497): **atomic** writes (temp file + `os.replace`), an in-memory handle→ref index built once on init so `list_refs` never rescans the directory, and optional `max_bytes` / `max_artifacts` quotas raising `ArtifactStoreQuotaError`. Persists `content_hash` and percent-encodes handles into filenames so the firewall's `artifact:result:…` handles are Windows-safe (issue #466). |
| `store/_json_file_io.py` | Private filesystem helpers for `json_file_artifacts.py` (keeps it ≤300 lines): on-disk suffix constants, `validate_handle` (path-traversal defense), `encode_handle` (percent-encoding), and the `atomic_write` primitive (issues #466/#497). Not public API. |
| `store/testing.py` | Store-protocol conformance kit (issue #520): framework-agnostic `check_event_log_conformance` / `check_artifact_store_conformance` / `check_episodic_store_conformance` / `check_fact_store_conformance`, each taking a factory for an empty backend and asserting the round-trip / ordering / not-found contract. No test-framework import; ships in the core wheel. `tests/test_store_conformance.py` runs every bundled backend through it. |
| `summarize/` | `SummarizationRule`, `RuleEngine`, `extract_facts()` |
| `summarize/structured.py` | Lossless JSON field projection for the firewall: `parse_path` / `project` + `StructuredFirewall(keep=[...])`. Deterministic, no LLM — keeps an allow-list of JSON paths inline and offloads the rest (issue #406). |
| `context/` | Full context pipeline, sensitivity enforcement, view registry, `ContextManager` |
| `context/firewall_api.py` | Single-call firewall facade: `compact_tool_result` / `firewalled_tool_result` → `CompactResult`. Composes structured/text strategies, schema-preserving pass-through (reserved `_cw` sidecar — a caller payload already using `_cw` raises `ConfigError` unless `overwrite_sidecar=True`, #467), the built-in token counter, and fail-closed `deterministic` mode (issues #399, #402, #403, #404, #405, #406, #467). |
| `context/manager.py` | `ContextManager` — thin orchestrator (`__init__`, properties, `drilldown`, mixin composition). Public method stubs live in flat partial-class mixins; pipeline logic lives in the delegate modules below (issue #101). |
| `context/_manager_base.py` | `_ManagerState` — private-attribute + `_build` contract the manager mixins inherit and the delegate pipeline modules type their `manager` parameter against (`ContextManager` inherits it via the mixins). Not public API (issue #101). |
| `context/_manager_ingest.py` / `_manager_build.py` / `_manager_routing.py` | `_IngestMixin` / `_BuildMixin` / `_RoutingMixin` — partial-class mixins holding `ContextManager`'s ingestion, build, and route/call-prompt method surface as thin delegations; keep `manager.py` ≤300 lines (issue #101). Not public API. |
| `context/ingest.py` | Tool-result ingestion helpers (extracted from `manager.py` to honor the <=300 line guideline). Includes `ingest_envelope` — the canonical Frame-shaped seam (weaver-spec I-05) that ingests an already-firewalled `ResultEnvelope` without re-deriving firewalling; raw-output `ingest_tool_result` / `ingest_mcp_result` are non-canonical for spec compliance (issue #352). |
| `context/memory_types.py` | `MemoryEntry` dataclass + `PHASE_SCOPE_PREFERENCES` constants for phase-aware memory ingestion (issue #293). |
| `context/memory_fixture.py` | `JsonFixtureMemorySource` — deterministic stdlib fixture adapter implementing the `MemorySource` Protocol from `protocols.py` (issue #293). |
| `context/memory_source.py` | `memory_entries_to_context_items` / `select_memory_for_phase` helpers that materialise memory entries into budgeted `memory_fact` candidates (issue #293). |
| `context/handoff_types.py` | `HandoffEntry` + `SessionHandoffPack` dataclasses and canonical handoff category constants (issue #294). |
| `context/handoff.py` | `build_session_handoff_pack` / `render_handoff_pack` — deterministic, budget-aware, sensitivity- and firewall-respecting session continuity snapshot (issue #294). |
| `context/consolidation_types.py` | `ConsolidationPolicy` / `EpisodeCluster` / `PromotedFact` / `ConsolidationReport` (+ `CONSOLIDATION_REPORT_VERSION`) — pure-data config and result types for the memory consolidation engine (issue #498). |
| `context/consolidation.py` | Memory consolidation engine (issue #498): `cluster_episodes` (deterministic episodic clustering/dedupe, #679), `promote_clusters` (fact promotion with provenance + max-sensitivity inheritance, #680; optional fail-closed `call_fn` merge, #682), `decay_episodes` / `decay_facts` (report-only decay over append-only stores, #681), and the `consolidate(...)` orchestrator → `ConsolidationReport`. Deterministic; `apply=True` upserts content-addressed facts (idempotent). Standalone functions (not a `ContextManager` method) mirroring `handoff.py`. |
| `context/_consolidation_helpers.py` | Private deterministic helpers for `consolidation.py` (clustering canonical text, max-sensitivity, session counting, ISO-timestamp parsing, content-addressed fact IDs, decay predicate) — keeps `consolidation.py` ≤300 lines. Not public API. |
| `context/_consolidation_merge.py` | Private optional model-assisted canonicalizer for consolidation (issue #682): `refine_canonical_text` runs a user-supplied `call_fn` under fail-closed guardrails (no LLM SDK dep; rejects blank/ungrounded completions that introduce tokens absent from the source cluster, falling back to the deterministic text). Not public API. |
| `context/explanation.py` | `ContextBuildExplanation` + `CandidateExplanation` opt-in debug surface returned by `ContextManager.build(..., explain=True)` (issue #291); carries `resolved_weights` (the per-phase scoring weights applied, issue #487). Sister to `routing/explanation.py` on the routing side. |
| `context/build_policy.py` | Pure build-pipeline policy helpers (not public API): `override_phase_budget` / `adjust_budget_for_header` (budget math), `enforce_overflow_policy` (`ContextPolicy.overflow_action`, issue #510), and `render_pack_prompt` (caller-owned `renderer` hook, issue #410). Extracted from `build.py` to keep it within its size ceiling. |
| `context/classify.py` | Opt-in deterministic ingestion-time sensitivity classification (issue #542): `HeuristicSensitivityClassifier` (implements the `SensitivityClassifier` protocol) + `detect_sensitivity()`. Runs at the start of the pipeline's sensitivity stage and over fact/episode header content; may only **raise** a label, never lower it. Reuses `secrets.contains_secret` plus PII markers. |
| `context/secret_redaction.py` | Opt-in `SecretRedactor` `RedactionHook` (issue #428): substring-scrubs secret shapes from an item's text via `secrets.scrub_secrets`. Registered under the name `"secret"` for `ContextPolicy.redaction_hooks`; complements (does not replace) `MaskRedactionHook`. |
| `routing/` | `Catalog`, `ChoiceGraph`, `TreeBuilder`, `Router` (beam search), card renderer |
| `routing/filters.py` | Pre-scoring helpers: `filter_items()`, `augment_query()`, `suggest_clarifying_question()` (issues #14, #22, #112, #116) |
| `routing/manifest.py` | `GraphManifest` + `compute_catalog_hash()` for graph metadata and cache invalidation (issue #48, #15) |
| `routing/normalizer.py` | `CatalogNormalizer` + `NormalizationReport` for catalog metadata hygiene (issue #44) |
| `routing/catalog.py` (validation) | `validate_references` / `Catalog.validate_references` → `CatalogValidationReport` of dangling `depends_on`/`requires` refs; loaders take `on_invalid` (`"warn"`/`"raise"`/`"ignore"`) and raise `CatalogValidationError` in raise mode (issue #519) |
| `routing/registry.py` | `EngineRegistry` and bundled `TfIdfRetriever` / `NoOpReranker` / `JaccardClusteringEngine` defaults (issue #47) |
| `routing/index_cache.py` | Persistent, reusable fitted-index cache (issues #543/#624/#685): `RoutingIndexCache` (in-process LRU + optional deterministic-JSON on-disk layer) and `CachedRetriever` (a `Retriever` wrapper that loads/stores the fitted index keyed by a corpus fingerprint, transparently — warm loads score byte-identically to a cold fit). Pass via `Router(retriever=CachedRetriever(TfIdfRetriever(), cache))`. Codec + fingerprint live in `routing/_index_codec.py`. |
| `routing/_index_codec.py` | Private helper for `index_cache.py`: `index_fingerprint()` (deterministic ordered-corpus SHA-256) + the `IndexCodec` contract and bundled `TFIDF_CODEC`. Not public API; names re-exported from `index_cache`. |
| `routing/trace.py` | `RouteTrace` + `TraceStep` structured routing audit (issue #51) |
| `routing/explanation.py` | `RouteResult.explanation()` Markdown / dict rendering (issue #226) |
| `routing/pipeline.py` | `RoutingPipeline` composer — explicit retrieve → rerank → navigate → pack stages (issue #56) |
| `routing/navigator.py` | `BeamSearchNavigator` (lifted from `router.py`) + `rank_collected` — the score-sort/active-filter helper is re-exported from `routing/__init__.py` for custom Navigator implementations (issues #56, #288) |
| `routing/packer.py` | `DefaultCardPacker` wrapping `make_choice_cards` for the pipeline pack stage (issue #56) |
| `routing/history.py` | `RouteHistory` dataclass + `adjust_scores` (history-aware re-routing, issue #27) |
| `routing/feedback.py` | Optional feedback-aware routing scores (issue #318): `ExecutionFeedback` (contextweaver-native, **not** a weaver-spec type), `DeterministicScoreProvider` (default no-op), `FeedbackAwareScoreProvider`, `aggregate_feedback`. Plugs into `Router(score_provider=...)`; default `None` keeps routing deterministic. |
| `routing/selection.py` | Structured route→select contract (issues #515/#479), both pure/deterministic: `selection_schema` emits the routed candidate IDs as a provider-native constrained-selection schema (`json_schema`/`openai`/`anthropic`) so a model can only pick a routed `tool_id` ("constrain before"); `validate_selection` → `SelectionValidation` validates/repairs (strip → case-fold → unique-prefix; ambiguous matches rejected, never guessed) a returned ID against the candidates ("validate after"). Surfaced on `RouteResult.selection_schema()` / `RouteResult.validate_selection()`; `to_routing_decision` resolves + records the outcome. Shortlist composition (`pin_ids` always-include + per-namespace `namespace_quota`, issue #509) lives in `routing/filters.compose_shortlist` and is exposed via `Router.route(...)`. |
| `extras/embeddings.py` | `SentenceTransformerBackend` + `HybridEmbeddingRetriever` + `HashingEmbeddingBackend` (re-exported) behind the `[embeddings]` extra (issue #8) |
| `extras/embeddings_hashing.py` | `HashingEmbeddingBackend` — stdlib-only deterministic `EmbeddingBackend` using blake2b hashing trick; no extras required (issue #266) |
| `_schema_gen.py` | Dataclass → JSON Schema (Draft 2020-12) generator + `make schemas-check` engine (issue #225) |
| `routing/tool_id.py` | Canonical `tool_id` grammar (`parse_tool_id` / `format_tool_id` / `compute_hash8`) per `docs/gateway_spec.md` §1 |
| `routing/primitive_id.py` | Unified cross-primitive identity + collision policy for tools/resources/prompts (`parse_primitive_id` / `format_primitive_id` / `canonical_resource_id` / `canonical_prompt_id` / `resolve_collisions`) per `docs/gateway_spec.md` §9. Tools keep the bare `tool_id`; resources/prompts get disjoint `kind::` ids (issue #671). |
| `routing/path.py` | `tool_browse` path-navigation grammar (`parse_path` / `resolve_path`) per `docs/gateway_spec.md` §3 |
| `routing/hydration.py` | Public schema-hydration helpers — `SchemaSource` (from raw dict / JSON file / MCP tools-list), `hydrate_with_schema`, `lazy_schema_resolver`. Reference architectures use these to resolve a tool's full input schema from a sidecar source rather than hand-rolling a `_FULL_SCHEMAS` dict. Inline `args_schema` on the catalog item wins; sidecar only fills empties. Issue #261. |
| `adapters/` | MCP, FastMCP, A2A, weaver-spec, CrewAI, Pydantic AI, smolagents, Agno, LangChain, OpenAI Agents SDK, Google ADK, Microsoft Agent Framework, OpenAPI, Agent Skills protocol adapters + MCP proxy / gateway runtime + provider-message ingestion helpers for OpenAI / Anthropic / Gemini chat histories. Framework tool-catalog adapters share `adapters/_framework_common.py` (issue #454). (issues #13, #28, #29, #34, #193, #194, #219, #222, #272, #274, #275, #430, #454, #501, #502, #545, #546, #547) |
| `adapters/chainweaver.py` | ChainWeaver flow-export → `SelectableItem(kind="flow")` import (`chainweaver_flow_to_selectable`, `chainweaver_flows_to_catalog`, `load_chainweaver_export`, issue #334). Pure data; no ChainWeaver dependency. Preserves name/description/input+output schemas; stamps `metadata["runtime"]="chainweaver"` + flow id/version. |
| `adapters/crewai.py` | CrewAI `BaseTool` (or equivalent plain-dict shape) ↔ `SelectableItem` (`crewai_tool_to_selectable`, `crewai_tools_to_catalog`, `infer_crewai_namespace`, `load_crewai_catalog`, issue #193) |
| `adapters/pydantic_ai.py` | Pydantic AI `Tool` ↔ `SelectableItem` and `ModelMessage` ↔ `ContextItem` lossless round-trip (`pydantic_ai_tool_to_selectable`, `pydantic_ai_tools_to_catalog`, `load_pydantic_ai_catalog`, `from_/to_pydantic_ai_messages`, issue #272) — heavy decode/encode helpers live in `adapters/_pydantic_ai_messages.py` |
| `adapters/smolagents.py` | Hugging Face smolagents `Tool` ↔ `SelectableItem` and `MultiStepAgent.memory.steps` → `ContextItem`s (`smolagents_tool_to_selectable`, `smolagents_tools_to_catalog`, `load_smolagents_catalog`, `from_smolagents_agent`, issue #274) |
| `adapters/agno.py` | Agno (formerly Phidata) `Function` / `Toolkit` ↔ `SelectableItem` and `AgentSession` → `ContextItem`s (`agno_tool_to_selectable`, `agno_tools_to_catalog`, `load_agno_catalog`, `from_agno_session`, issue #275) |
| `adapters/_framework_common.py` | Shared, framework-agnostic conversion scaffolding for the framework tool-catalog adapters (issue #454): `infer_namespace`, `strip_namespace_prefix`, `coerce_schema_dict`, `collect_tags`, `require_name_description`. Pure/stateless, imports no framework lib. Private — not exported. New adapters reuse these instead of re-implementing namespace/schema/tag mechanics. |
| `adapters/langchain.py` | LangChain `BaseTool` (or equivalent plain-dict shape) ↔ `SelectableItem` (`langchain_tool_to_selectable`, `langchain_tools_to_catalog`, `infer_langchain_namespace`, `load_langchain_catalog`, issue #502). `[langchain]` extra for live loading; plain-dict path needs no extra. |
| `adapters/openai_agents.py` | OpenAI Agents SDK `FunctionTool` ↔ `SelectableItem` and run items → `ContextItem`s (`openai_agents_tool_to_selectable`, `openai_agents_tools_to_catalog`, `load_openai_agents_catalog`, `from_openai_agents_run`, issue #501). Run-item ingestion lives in `adapters/_openai_agents_run.py`. `[openai-agents]` extra for live loading. |
| `adapters/google_adk.py` | Google ADK tools ↔ `SelectableItem` and `Session.events` → `ContextItem`s (`google_adk_tool_to_selectable`, `google_adk_tools_to_catalog`, `load_google_adk_catalog`, `from_google_adk_session`, issue #547). Session ingestion lives in `adapters/_google_adk_session.py`. `[google-adk]` extra for live loading. |
| `adapters/agent_framework.py` | Microsoft Agent Framework (AutoGen / Semantic Kernel lineage) tools ↔ `SelectableItem` and thread `ChatMessage`s → `ContextItem`s (`agent_framework_tool_to_selectable`, `agent_framework_tools_to_catalog`, `load_agent_framework_catalog`, `from_agent_framework_thread`, issue #430). Thread ingestion lives in `adapters/_agent_framework_thread.py`. `[agent-framework]` extra for live loading. |
| `adapters/openapi.py` | OpenAPI 3.0/3.1 operations → `SelectableItem` catalog (`openapi_operation_to_selectable`, `openapi_spec_to_catalog`, `load_openapi_catalog`, `infer_openapi_namespace`, issue #546). Routes over REST APIs; never calls them. Local `$ref` resolution + `parameters`/`requestBody` → `args_schema` composition + method→safety tags live in `adapters/_openapi_schema.py`. No extra — PyYAML/jsonschema are core. |
| `adapters/agent_skills.py` | Agent Skills (`SKILL.md`) directories → `kind="skill"` `SelectableItem`s with lazy body hydration (`skill_to_selectable`, `load_skills_catalog`, `parse_skill_frontmatter`, `SkillBodySource`, issue #545). Frontmatter routes; `SkillBodySource` resolves the body/resources on selection (mirrors `routing/hydration.SchemaSource`). No extra — PyYAML is core. |
| `adapters/proxy_runtime.py` | `ProxyRuntime` shared core + `ExposureMode` enum + `UpstreamCall` Protocol (issue #29) |
| `adapters/gateway_diagnostics.py` / `gateway_catalog_diagnostics.py` | Sanitized `ProxyRuntime` instrumentation plus exact gateway/proxy static-schema exposure calculations: catalog, browse/hydrate/execute/view events, savings, artifact-view usage, and latency (issues #370/#378). |
| `adapters/mcp_gateway.py` | Two-tool gateway dispatch (`tool_browse` + `tool_execute` + `tool_view`, issues #28 / #34) |
| `adapters/mcp_proxy.py` | Transparent proxy dispatch (stripped `tools/list` + `tool_hydrate` + `tool_execute`, issue #13) |
| `adapters/mcp_upstream.py` | Concrete `UpstreamCall` adapters (`StubUpstream`, `McpClientUpstream`, `MultiplexUpstream`) |
| `adapters/mcp_gateway_server.py` | Bind `mcp_gateway` onto `mcp.server.Server` over stdio (issue #28); optional `primitive_runtime=` also advertises/dispatches the four resource/prompt meta-tools (issues #669/#670) |
| `adapters/mcp_primitives.py` | MCP resource/prompt → `SelectableItem(kind="resource"/"prompt")` converters + `resources/read` / `prompts/get` result→envelope wrappers (issues #669/#670). Emits ids via `routing/primitive_id`. |
| `adapters/gateway_primitives.py` | `PrimitiveGatewayRuntime` + `PrimitiveUpstream` Protocol — bounded-choice routing + firewall for resources/prompts, sharing the tool runtime's `ContextManager` (issues #669/#670/#555). |
| `adapters/_primitive_index.py` | Private single-kind catalog+graph+router+browse helper for `gateway_primitives` (keeps it ≤300 lines). Not public API. |
| `adapters/mcp_gateway_primitives.py` | The four resource/prompt gateway meta-tools (`resource_browse` / `resource_read` / `prompt_browse` / `prompt_get`) + dispatch, mirroring `mcp_gateway` (issues #669/#670). |
| `adapters/mcp_primitive_upstream.py` | Concrete `PrimitiveUpstream` adapters mirroring `mcp_upstream`: `StubPrimitiveUpstream` (in-process), `McpClientPrimitiveUpstream` (wraps an MCP `ClientSession`), `MultiplexPrimitiveUpstream` (multi-server fan-out). Transport errors raise (the runtime classifies them) per the Protocol contract (issues #669/#670). |
| `adapters/mcp_proxy_server.py` | Bind `mcp_proxy` onto `mcp.server.Server` over stdio (issue #13) |
| `adapters/sidecar_contract.py` | HTTP sidecar wire contract (issue #674): `RouteRequest`/`RouteResponse`/`CompactRequest`/`CompactResponse`/`SidecarError` dataclasses + `SIDECAR_API_VERSION`. Pure, dependency-free; the published JSON Schemas live under `schemas/sidecar/v1/`. |
| `adapters/sidecar.py` | HTTP sidecar runtime (issue #675/#676): `SidecarConfig` + `SidecarApp.dispatch` — transport-free `(method, path, headers, body) → (status, json)` over the sync `Router` (`/v1/route`) and `compact_tool_result` facade (`/v1/compact`). Optional bearer-token auth, per-client rate limiting (reuses `gateway_controls.RateLimiter`), body-size cap, and typed `SidecarError` responses; never raises across the HTTP boundary. |
| `adapters/_sidecar_http.py` | Stdlib `http.server.ThreadingHTTPServer` binding for `SidecarApp` (issue #675). No third-party dependency. Public re-exports: `serve_api` (blocking serve) + `make_sidecar_server` (build-only, for tests). Not public API itself. |
| `adapters/_sidecar_validation.py` | Stateless parsing + field-validation helpers shared by `sidecar.py` and `sidecar_contract.py` (request-body JSON decode, bearer-token extraction, typed contract-field coercions). Pure, dependency-free; raises `ConfigError` on malformed input. Not public API. |
| `adapters/gateway_error.py` | Structured `GatewayError` (codes + §3.4 wire shape) + `retryable` hint. Upstream-error taxonomy: `classify_upstream_exception` maps timeouts/connection/auth/permission/rate failures to `UPSTREAM_TIMEOUT`/`UPSTREAM_UNAVAILABLE`/`AUTH_FAILED`/`PERMISSION_DENIED`/`RATE_LIMITED` (fallback `UPSTREAM_ERROR`); `redact_upstream_detail` strips control chars + caps length on model-visible detail (issue #485). |
| `adapters/gateway_validation.py` | Untrusted-schema hardening for the gateway ingest path (issues #464/#484): `SchemaLimits`/`SchemaFinding`/`SkippedTool`/`CatalogRefreshReport`, `check_schema_health` (meta-validation + iterative size/depth/property bounds), `build_validator` (cached per `tool_id`). Pure, deterministic; iterative traversal avoids stack exhaustion on hostile schemas. |
| `adapters/gateway_args.py` | Opt-in deterministic tool-call argument repair (issue #488): `normalize_args` (stringified-object parse + schema-demanded `str→int/number/boolean/null` coercion) + `Repair`. Gated behind `ProxyRuntime(tolerant_args=True)`; never renames keys, drops keys, or fuzzy-matches. |
| `adapters/gateway_policy.py` | Pure-data config + result types for the dispatch-path controls (issues #529/#482/#483): `RetryPolicy` (bounded backoff), `RateLimit`/`RateLimitPolicy` (per-session quotas), `DryRunReport`. All defaults inert; `to_dict`/`from_dict` for `mcp serve --config`. |
| `adapters/gateway_controls.py` | Runtime mechanisms behind `gateway_policy` (issues #529/#482/#512): `call_with_retry` (retry loop, injectable `sleep`), `RateLimiter` (sliding-window + cumulative counters, injectable `clock`), `ToolResultCache` (TTL+LRU read-only response cache). All opt-in; wired into `ProxyRuntime.execute`/`browse`/`view`. |
| `adapters/openai_messages.py` | OpenAI Chat Completions `messages` ↔ `ContextItem` round-trip (`from_/to_openai_messages`, issue #219) |
| `adapters/anthropic_messages.py` | Anthropic Messages API `messages` ↔ `ContextItem` round-trip (`from_/to_anthropic_messages`, issue #222) |
| `adapters/gemini_contents.py` | Google Gemini `contents[]` ↔ `ContextItem` round-trip (`from_/to_gemini_contents`, issue #222) |
| `extras/otel.py` | OpenTelemetry GenAI integration (`OTelEventHook` — `invoke_agent` / `execute_tool` spans + GenAI SemConv attributes, gated behind the `[otel]` extra, issue #224). |
| `extras/llm_summarizer.py` | Optional `LlmSummarizer` / `LlmExtractor` — LLM-backed `Summarizer` / `Extractor` plugins for the firewall. Take a user-supplied `call_fn` (no LLM SDK dep, no extra) and degrade to the rule-based path on any failure (issue #26). |
| `extras/memory/` | External-memory backend adapters that implement `EpisodicStore` / `FactStore` against an existing long-lived memory deployment without widening the Protocols (issue #195). |
| `extras/memory/mem0.py` | `Mem0EpisodicStore` + `Mem0FactStore` — wrap a `mem0.Memory` instance scoped by `user_id`; writes go through `Memory.add(infer=False)` and items are stamped with `cw_episode_id` / `cw_fact_id` metadata for canonical-ID resolution. Gated behind the `[mem0]` extra (issue #195). |
| `extras/memory/zep.py` | `ZepEpisodicStore` + `ZepFactStore` — wrap a `zep_cloud.Zep` client scoped by `user_id`; persist items as JSON graph episodes (`graph.add(type="json")`) stamped with `cw_*` IDs, resolving back via `graph.episode.get_by_user_id`. Episodic `search` is client-side (Zep graph search is edge/node-shaped). Gated behind the `[zep]` extra (issue #195). |
| `extras/memory/_zep_common.py` | Internal helpers backing `zep.py` (keeps it ≤300 lines): shared `cw_*` constants, the `ZepBackendError` exception, the JSON/scan helpers (`_episode_records` / `_episode_uuid` / `_episode_payload`), the defensive payload-coercion helpers (`_coerce_str_tags` / `_coerce_metadata`), and the `_ZepStoreBase` scope/scan/write base. Carries the same `[zep]`-extra import guard (issue #195). |
| `extras/memory/langmem.py` | `LangMemEpisodicStore` + `LangMemFactStore` — wrap any LangGraph `BaseStore` scoped by a `namespace` tuple; canonical ID is the store key, value is the dataclass `to_dict()` payload (direct, lossless KV). `search` delegates to `BaseStore.search`. Gated behind the `[langmem]` extra (issue #195). |
| `eval/` | Evaluation harness (issue #12): `EvalCase` / `EvalDataset` (gold datasets), `evaluate_routing` → `RoutingEvalReport` (top-k recall, MRR, confidence gap, beam steps), `evaluate_context` → `ContextEvalReport` (budget utilisation + token savings vs naive concat). Pure-stdlib, deterministic; backs the `eval` CLI subcommand. |
| `eval/consolidation.py` | Consolidation quality evaluation harness (issue #683): `evaluate_consolidation` → `ConsolidationEvalReport` (precision / coverage against an optional gold set + dedup ratio). Pure-stdlib, offline, deterministic. |
| `eval/metrics.py` | Canonical rank-based routing metrics — `recall_at_k` (classic fractional recall@k), `precision_at_k`, `reciprocal_rank` (issue #354). Single source of truth imported by both `eval/routing.py` and `benchmarks/benchmark.py` so the harness and the benchmark script can no longer define the same names with different semantics. |
| `__main__.py` | CLI: 11 subcommands (`demo`, `build`, `route`, `print-tree`, `init`, `ingest`, `replay`, `stats`, `inspect`, `budget-check`, `eval`) plus the `mcp` and `catalog` Typer sub-apps. `inspect` renders payload-safe context/routing/artifact JSON or Markdown (issue #398); `catalog lint` surfaces `NormalizationReport` + reference findings with `--json` and CI exit codes (issue #538). |
| `_mcp_cli.py` | Backs the `mcp` Typer sub-app. Hosts `mcp serve`, `mcp inspect`, `mcp stats`, and `mcp generate-configs`; accepts native contextweaver, raw MCP `tools/list`, and `{tools:[...]}` catalog shapes. `mcp serve --diagnostics FILE` appends sanitized JSONL and `--quiet` suppresses lifecycle stderr; both are config-file keys. `mcp serve --state-dir DIR` (config key `state_dir`) persists gateway state — `events.sqlite3` + `artifacts/` — so artifact handles and event history survive a restart (issue #511); omit it for the in-memory default. `mcp generate-configs` emits deterministic multi-client recipe artifacts from one canonical `mcp serve --config` input (issue #659). |
| `data/` | Packaged data files shipped inside the wheel via `[tool.setuptools.package-data]`. Exposes `gateway_catalog_path()` (resolves `mcp_gateway_catalog.yaml` to a concrete `Path` for both editable installs and zipped wheels — falls back to a persistent cache under `tempfile.gettempdir()/contextweaver/` for zipimport). Issue #264. |
| `examples/recipes/` | MCP-client integration recipes: installed-CLI configs for Claude Desktop, Claude Code, GitHub Copilot, and Cursor plus `gateway_config.yaml`; `serve_gateway.py` remains a legacy/custom-runtime launcher (issues #278, #279, #346, #371, #429, #437). |

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
| `SelectableItem` | Unified tool/agent/skill/flow/internal item (`kind="flow"` = external multi-step capability, e.g. a ChainWeaver flow). Deprecated alias: `ToolCard` (use `SelectableItem` in code; removal in 1.0 — see [docs/upgrading.md](docs/upgrading.md)). |
| `ContextItem` | Event log entry with `parent_id` for dependency closure |
| `ResultEnvelope` | Processed tool output: summary + facts + artifacts + views |
| `ContextPack` | Rendered prompt + stats from a context build |
| `BuildStats` | What was kept, dropped, and why. `total_candidates` is pre-sensitivity; `dropped_items` attributes every exclusion; completed builds satisfy included + dropped = total. Carries `firewall_events` + `firewall_summary()` (issues #402/#414/#459) and `token_estimator` — the estimator-path identifier that produced the build's token numbers (issue #493). |
| `FirewallStats` | Per-firewall diagnostics: `triggered`, `strategy`, original/summary chars+tokens, `artifact_ref`, `summarized_by_llm` (issues #402 / #404) |
| `CompactResult` | Output of the single-call `compact_tool_result` facade: `firewalled`, `payload`, `summary`, `facts`, `artifact_ref`, `stats` (issue #399) |
| `StructuredFirewall` | Non-summarising firewall strategy — keep an allow-list of JSON paths inline, offload the rest (issue #406) |
| `ChoiceCard` | LLM-friendly compact card (never includes full schemas) |
| `RoutingDecision` | Routing output shaped for weaver-spec interop (id, choice_cards, timestamp, selection). `choice_cards` is a flat list of CW 1:1 cards; for schema-valid spec JSON, go through `adapters.weaver_contracts.to_weaver_routing_decision()`. Build with `RouteResult.to_routing_decision(...)`. |
| `ChoiceGraph` | Bounded DAG for routing, serializable, validated on load |
| `GraphManifest` | Build-time metadata attached to every routing graph (hash, seed, engine versions, timestamp) |
| `RouteTrace` | Always-populated structured audit of a routing call; per-step expansions opt-in via `debug=True` |
| `EngineRegistry` | Pluggable registry for `Retriever`, `Reranker`, `ClusteringEngine` slots |
| `Mode` | Determinism mode (`strict` / `seeded` / `adaptive` placeholder) on `ProfileConfig` |
| `MaskRedactionHook` | Built-in redaction hook for sensitivity enforcement |
| `HydrationResult` | Result of hydrating a tool call with context |
| `ConsolidationReport` | Deterministic result of a `consolidate()` run: episode clusters, promoted facts (with provenance + inherited sensitivity), and report-only decayed episode/fact IDs (issue #498) |
| `ViewRegistry` | Maps content-type patterns to view generators for progressive disclosure |
| `ProxyRuntime` | Shared core for MCP proxy (#13) and gateway (#28) modes — owns upstream catalog, per-session `ContextManager`, browse / execute / view dispatch; persisted text results are returned as envelope artifact refs for `tool_view`. Hardens the untrusted-input boundary (issues #464/#484/#485/#488): `on_invalid` (skip/raise) + `schema_limits` + `last_refresh_report` at ingest, cached per-`tool_id` validators, classified+redacted upstream errors, and opt-in `tolerant_args`. Opt-in dispatch-path controls (issues #529/#482/#512/#483): `retry_policy`, `rate_limiter`, `result_cache`, and `tool_execute(dry_run=True)` — all inert by default; catalog refresh rebuilds all derived state atomically (#507). |
| `ExposureMode` | `TRANSPARENT` (#13) vs `GATEWAY` (#28) for `ProxyRuntime` |
| `UpstreamCall` | Transport-agnostic Protocol over upstream MCP fan-out (used by `ProxyRuntime`) |
| `PrimitiveGatewayRuntime` | Resource/prompt counterpart to `ProxyRuntime`: bounded-choice browse + firewalled read/get over MCP resources and prompts, sharing the tool runtime's `ContextManager` (issues #669/#670) |
| `PrimitiveUpstream` | Transport-agnostic Protocol for upstream `resources/list` / `resources/read` / `prompts/list` / `prompts/get` (sibling of `UpstreamCall`) |
| `GatewayError` | Structured error payload (§3.4) returned from every gateway/proxy meta-tool. Carries a `retryable` hint and a classified upstream-error taxonomy (issue #485) plus the `SCHEMA_INVALID` ingest code (issue #484) |
| `ToolIdParts` | Destructured canonical `tool_id` (namespace / name / version / hash8) |

**Vocabulary notes:**
- `SelectableItem` is the canonical name. `ToolCard` is a **deprecated** alias (documentation-only deprecation, scheduled for removal in 1.0 — see [docs/upgrading.md](docs/upgrading.md)) — use `SelectableItem` in code and docs.
- "Context" is overloaded — can mean `ContextItem`, `ContextPack`, the pipeline, or the LLM context window. Disambiguate when unclear. See [docs/concepts.md](docs/concepts.md).
- "Firewall" here means context firewall (prevents large outputs from consuming the token budget), not a security firewall.

## Commands

```bash
make fmt      # ruff format src/ tests/ examples/ scripts/
make lint     # ruff check src/ tests/ examples/ scripts/
make type     # mypy src/ examples/ scripts/  (examples + scripts gated too, #539)
make test     # python -m pytest --cov=contextweaver --cov-report=term-missing -q
make example  # run all example scripts (includes architectures via the umbrella target)
make architectures  # run reference architecture scripts under examples/architectures/
make demo     # python -m contextweaver demo
make ci       # fmt + lint + type + test + drift-check + module-size-check + doc-snippets-check + readme-version-check + security-policy-check + example + demo
make docs     # mkdocs build --clean (docs site)
make docs-serve  # mkdocs serve (live preview)
make benchmark        # run benchmark harness (non-gating; writes benchmarks/results/latest.json)
make benchmark-matrix # benchmark + per-backend × per-size matrix (#208) and per-namespace breakdown (#209)
make gateway-scorecard-check  # verify gateway scorecard matches its committed JSON (gating CI; #391)
make record-demos-check       # verify committed demo casts match current output (gating CI; #390)
make smoke-eval       # non-gating CI smoke-evaluation over fixed fixtures (#331/#392); deterministic, credential-free
make scorecard        # render benchmarks/scorecard.md from benchmarks/results/latest.json
make scorecard-check  # verify scorecard.md is up to date (exits non-zero on drift)
make schemas         # regenerate schemas/ + docs/schemas/v0/ (issue #225)
make schemas-check    # verify published schemas match dataclasses (gating, in `make ci`)
make drift            # regenerate every committed generated artifact (issue #522)
make drift-check      # one gate over all generated-artifact drift checks (in `make ci`, #522)
make api             # regenerate api/public_api.txt (public-API manifest, #518)
make api-check        # verify the public-API manifest matches the surface (in drift-check, #518)
make module-size-check # enforce the ≤300-line convention; frozen baseline (gating, #456)
make module-size-update # re-snapshot scripts/module_size_baseline.json (deliberate use only)
make doc-snippets-check # execute README + curated docs Python snippets (gating, #526)
make sweep-scoring    # weight sweep for ScoringConfig (#214); writes benchmarks/sweep_scoring.md
make context-rot       # render the context-rot demo: benchmarks/results/context_rot.json + docs/assets/context_rot.svg (#349)
make context-rot-check # verify context_rot.svg matches its committed JSON (gating in CI; exits non-zero on drift)
make readme-version-check  # verify README version references match pyproject.toml (gating in CI; #347)
make security-policy-check # verify SECURITY.md supported series + links match pyproject.toml (gating in CI; #691)
make llms        # regenerate llms.txt and llms-full.txt from canonical docs
make llms-check  # verify llms.txt and llms-full.txt are up to date (gating in CI; #389)
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
- **All exceptions from `contextweaver.exceptions`** — use the custom hierarchy, not bare `ValueError`/`RuntimeError`. A new exception class needs a unique stable `code`, a `GOLDEN_CODES` entry in `tests/test_exceptions.py`, and a section in `docs/errors.md`.
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
  Enforced mechanically by `make module-size-check` (issue #456): new
  non-exempt modules must stay ≤300 lines, and pre-existing oversized modules
  are **grandfathered** at their current size in
  `scripts/module_size_baseline.json` and frozen — they may shrink but may not
  grow past their recorded ceiling. Decomposing a grandfathered module lowers
  its ceiling via `make module-size-update`.
- **Core runtime dependencies.** The core install pulls `tiktoken`, `PyYAML`, `rank-bm25`, plus `mcp` and `jsonschema` (added when the proxy / gateway runtimes landed — both are load-bearing for `docs/gateway_spec.md` §4.4 schema validation and the MCP transport binding).  Adding *another* core dependency requires explicit justification: broad ecosystem use, small wheel, and a default the library would otherwise have to approximate.  Heavy or runtime-specific packages (CLI, OpenTelemetry, fuzzy retrieval, ANN, NetworkX, FastMCP, LangChain) live under `[project.optional-dependencies]` and are loaded via guarded imports.
- **Dependency-constraint policy** (issue #356). Specifiers are **lower-bound-only** (`>=`), set to the lowest version *actually known to work* — no `==` pins, no speculative upper caps. The only caps kept carry an inline rationale (pre-1.0 `weaver_contracts<1`; docs-extra major pins). Two CI jobs enforce this: a **gating floor-deps job** (`uv pip install --resolution lowest-direct`, Python 3.10, in `ci.yml`) proves the `>=` floors, and a **non-gating weekly** `deps-latest-weekly.yml` (latest + pre-releases) is the no-upper-cap safety net. Raising a floor is a real change — verify with the floor-deps job. Python support is **3.10–3.13**, every cell gating in the CI matrix (3.14 is pending — the heavy dev/adapter stack still caps at `Requires-Python <3.14`).

## Testing

- Tests in `tests/test_<module>.py` — one file per module.
- `pytest.mark.asyncio` for async tests (`asyncio_mode = "auto"` is set globally).
- Do not mock internal modules — use real in-memory implementations.

## Path Conventions

**`store/`** — Protocols are backend-agnostic (must not import backend-specific libraries). Concrete implementations may import backend libs. Must implement the protocol from `protocols.py`. Data is append-only / immutable-after-write.

**`adapters/`** — Pure stateless converters. External format parsing must not leak into core. May import optional external libraries at the adapter boundary only.

**`context/`** — Async-first. All new code should be async with `_sync` wrappers.

**`routing/`** — Sync-only. Pure computation (DAG traversal, beam search). Do not make async.

**Sensitivity (`context/sensitivity.py`)** — Security-grade code. Extra review scrutiny required. Never weaken defaults. Treat changes like security-sensitive code. Redaction is effective end-to-end: `MaskRedactionHook` drops the item's `artifact_ref` and stamps `metadata["redacted"]=True` so the rendered prompt never advertises a handle that `drilldown` could dereference back to the original (issue #451). Enforcement is also applied on the prompt **header** (facts + episode summaries are routed through the floor and the `memory_fact` phase policy, issue #450) and an opt-in `SensitivityClassifier` may raise labels before enforcement (issue #542).

## Things That Must Not Be "Simplified"

1. **Protocol-based store design** — the protocol layer exists for backend extensibility. Do not collapse protocols into concrete classes.
2. **`dependency_closure` pipeline stage** — if a selected item has `parent_id`, the parent must be included. Removing it produces incoherent context (tool results without their tool calls).
3. **`serde.py` + per-class `to_dict`/`from_dict`** — complementary, not redundant. `serde.py` provides shared primitives; per-class methods handle class-specific serialization. Do not consolidate.
4. **`ContextManager` mixin composition** — its public method surface lives in flat partial-class mixins (`_IngestMixin` / `_BuildMixin` / `_RoutingMixin`) sharing the `_ManagerState` base. Do not "simplify" this into delegating composition (`manager.ingest.x()`): that would change the public method surface, which issue #101 forbids ("ContextManager still exposes all current methods"). Mixins were the deliberate trade-off to keep `manager.py` ≤300 lines without breaking the public API.

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
2. Run `make ci` to verify (all declared targets must pass).
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

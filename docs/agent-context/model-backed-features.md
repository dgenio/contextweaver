# Deterministic-First Rubric for Model-Backed Features

> **Scope:** every proposal that would make contextweaver call an LLM,
> embedding model, or trained ranker — at build time, at runtime, or in a CLI.
> **Authority:** this rubric operationalises the "deterministic by default"
> invariant in [invariants.md](invariants.md). When a proposal and this page
> disagree, this page wins until the maintainer amends it. (Issue #505.)

## The core rule

contextweaver's core pipelines are deterministic, offline, and LLM-free.
A model may **assist** — rank, retrieve, cluster, summarize, suggest — but a
model must never **decide** — authorize, execute, redact, filter by policy,
or become the only copy of data.

## Acceptance rubric

A model-backed feature proposal is acceptable only if every row holds:

| # | Requirement | Test |
|---|---|---|
| 1 | **Off by default** | A default install and default config never load or call the model. Enabling requires an explicit argument, config key, or extra. |
| 2 | **Deterministic fallback** | The same code path completes (possibly with lower fidelity) when the model is absent, fails, times out, or returns garbage. The fallback is the pre-existing deterministic behaviour, not a new approximation. |
| 3 | **Assist, never authorize** | The model's output can change *ordering, phrasing, or suggestions* — never visibility, policy, sensitivity, authorization, execution eligibility, or redaction outcomes. |
| 4 | **Raw data survives** | If the model transforms content (summaries, enrichment), the untransformed original remains stored and reachable (artifact handle, suggestion diff), and model output is labelled as model-produced. |
| 5 | **Guard envelope** | Runtime model calls go through `extras/llm_guard.GuardedCallFn` (issue #494): timeout accounting, call caps, circuit breaker. No unguarded `call_fn` in a loop. |
| 6 | **Auditable** | Provider/model/version metadata is recorded alongside any persisted model output. |
| 7 | **No new core dependency** | Model SDKs and ML libraries live under `[project.optional-dependencies]` with guarded imports (`extras/embeddings.py` is the reference pattern). Callers supply `call_fn`; contextweaver ships no LLM SDK. |
| 8 | **Testable offline** | CI exercises the feature with a deterministic stand-in (`HashingEmbeddingBackend`, a scripted `call_fn`) — no network, no model download, no credentials. |

## Keep-deterministic list

These stay deterministic permanently. Proposals to add model judgement to
them are rejected, not iterated:

- **Sensitivity enforcement and secret scrubbing** (`context/sensitivity.py`,
  `secrets.py`) — a model may *raise* a label via the `SensitivityClassifier`
  seam, never lower one, and never replaces the pattern-based scrubbers.
- **Policy, visibility, and authorization gates** (`gateway_authz`,
  `gateway_visibility`) — rule evaluation is pure and deterministic.
- **Budget enforcement and token accounting** (`tokens.py`, selection/packing).
- **Dependency closure, dedup, and pipeline stage ordering.**
- **Catalog validation, `tool_id` grammar, schema validation.**
- **The default routing path** — lexical retrieval, beam search, card packing.
  Embedding retrieval and trained rankers are opt-in candidate sources or
  re-orderers *after* deterministic eligibility filtering.
- **Artifact storage and `tool_view` slicing** — model summaries are views
  over artifacts, never replacements for them.

## Where model assist is welcome

- Optional retrieval/reranking after eligibility filtering (issues #8, #387,
  #388, #500).
- Optional summarization of firewalled results, raw artifact retained
  (issues #26, #384).
- Offline, human-reviewed metadata enrichment emitting suggestion diffs
  (issue #383).
- Advice-only escalation packs where a stronger model proposes and the
  caller decides (issue #741).

## How to propose

State in the issue/PR body: which rubric rows apply and how each is met,
which keep-deterministic entries the feature touches (must be "none"), the
fallback behaviour, and the offline test strategy. A proposal that cannot
fill in row 2 or row 3 is a redesign, not a review discussion.

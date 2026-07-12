# Compiler-first backlog audit — July 2026

Status: proposed operating baseline  
Repository: `dgenio/contextweaver`  
Audit date: 2026-07-12  
Inventory covered: 119 open issues

## Executive decision

ContextWeaver should be developed as an **offline capability compiler with a phase-aware context runtime**.

The product should:

1. discover capabilities from MCP/FastMCP, A2A Agent Cards, Agent Skills, OpenAPI, native functions, ChainWeaver assets, and the framework adapters already maintained by the project;
2. normalize those inputs into a framework-independent capability model;
3. analyse identity, ambiguity, dependencies, resources, schemas, provenance, and routing quality offline;
4. optionally enrich the normalized model through deterministic enrichers and reviewable LLM-produced proposals;
5. evaluate the candidate artifact before promotion;
6. compile a versioned, content-addressed agent bundle;
7. load the bundle at runtime to route, hydrate, and build context for the `route`, `call`, `interpret`, and `answer` phases.

The host runtime remains responsible for model loops, authentication, authorization, execution, retries, transactions, side effects, operational audit, and cancellation.

The existing MCP gateway is a supported transition surface, not the long-term product identity.

## North-star outcome

The north star is **correct capability selection and successful task completion with bounded, phase-appropriate context**.

Token reduction remains a useful measurement, but it is not the primary product claim. Current routing benchmarks are not yet strong enough to justify broad superiority claims, so quality gates and representative task evaluation precede distribution work.

## Scope guardrails

### Compiler-owned

- source discovery and snapshots;
- normalization and stable capability identity;
- duplicate, ambiguity, dependency, and resource analysis;
- deterministic and explicitly accepted enrichment;
- routing indexes and evaluation artifacts;
- versioned bundle, lock, provenance, diff, and drift detection;
- runtime routing, hydration, phase context compilation, and normalized result ingestion.

### Host-owned

- model/agent loop;
- credentials, identity, authentication, and authorization;
- tool/agent execution and transports;
- retries, timeouts, transactions, idempotency, and side effects;
- production enforcement, deployment control, and operational audit.

### Explicit non-goals for the compiler MVP

- becoming a general MCP control plane;
- owning production orchestration;
- executing arbitrary Python stubs generated from schemas;
- online revocation and deployment-policy infrastructure;
- conversation-memory, multi-agent memory, or vertical-agent products as core commitments;
- 10k-tool scale claims before routing quality is fixed at representative smaller scales.

## Release sequence

### 0.17 — Stabilize the boundary

- publish the compiler-first architecture decision;
- classify public APIs as `core`, `experimental`, `legacy`, or `internal-accidental`;
- freeze new gateway execution/platform features;
- establish adapter lifecycle rules and a lazy registry;
- keep security, correctness, compatibility, and migration fixes flowing.

### 0.18 — Compiled artifact MVP

- multi-source discovery and normalized source snapshots;
- canonical directory bundle with content-addressed components;
- manifest, lock, provenance, containment, and compatibility contracts;
- writer, loader, verifier, diff, and deterministic packaging;
- explicit external resource descriptors and host-provided resolution.

### 0.19 — Runtime and host seam

- `CompiledAgent.load(...)`;
- `route(...)`, `hydrate(...)`, `build_context(...)`, and `ingest_result(...)`;
- host executor and normalized-result protocols;
- phase-aware context contracts and restriction projection;
- no execution implementation in core.

### 0.20 — Evaluation, killer demo, and adoption proof

- generated routing evaluation datasets and gates;
- representative real-model/task-success evaluation;
- one end-to-end demo: MCP + OpenAPI + Agent Skill + framework capability + A2A agent → one bundle → route/hydrate/host-execute/interpret;
- compiler tutorial, evidence-based claims, and migration examples.

### 0.21 — Gateway deprecation and migration

- migrate docs and examples to compiler-first APIs;
- deprecate gateway execution entry points after replacement coverage exists;
- retain discovery/hydration compatibility where useful;
- decide whether reusable execution concepts move to a host example or another repository.

## Backlog classification

The classifications below are product-governance decisions, not claims that the underlying engineering work is complete.

### Rewrite as canonical compiler-first work

These issues remain open but their scope/title/body should be rewritten around the approved architecture:

- #376 — legacy gateway freeze, transition, deprecation, and extraction;
- #408 — `CompiledAgent` loader and phase-aware runtime API;
- #409 — offline analysis/compile report command;
- #433 — neutral landscape for capability discovery, model-side tool search, gateways, and context compilation;
- #434 — compiler-first killer demo and narrative tutorial;
- #477 — bundle and persisted-contract compatibility policy;
- #561 — host-provided outcome/evaluation ingestion, not autonomous runtime learning;
- #610 — public API inventory with `core`/`experimental`/`legacy`/`internal-accidental` levels;
- #651 — official adapter lifecycle, support window, conformance, and retirement policy;
- #758 — canonical offline capability compiler and phase-aware runtime epic.

### Keep — P0

These directly protect the compiler thesis, artifact correctness, routing quality, security, or maintainability:

- #440, #445, #453, #478, #480, #486, #489, #492, #499, #506, #527, #641, #717, #749, #752, #753, #754.

### Keep — P1

These are useful after or alongside the P0 path, without redefining the product:

- #359, #397, #412, #423, #441, #449, #452, #455, #457, #465, #475, #476, #481, #490, #503, #528, #537, #541, #559, #560, #586, #613, #614, #617, #619, #653, #747, #748, #755.

### Merge or absorb into canonical work

Do not execute these as independent product tracks. Preserve useful acceptance criteria by moving them into the referenced canonical epic or implementation track:

- #421 → #433 documentation/landscape;
- #431 and #532 → #434 runtime/provider examples;
- #436 → release/documentation operations after the 0.18 artifact contract;
- #470, #471, and #557 → runtime/compiler performance track;
- #500 and #544 → enrichment/evaluation track;
- #513 and #540 → source snapshot and bundle contracts;
- #536 and #740 → compiler CLI design after command shape stabilizes;
- #549 and #611 → #651 adapter lifecycle and compatibility matrix;
- #558 → #434 example consolidation;
- #618 → platform/reproducibility compatibility work;
- #759 → phase rendering and inference-aware context layout;
- #631 → #376 as a documented legacy capability; execution ownership is not expanded in core.

### Defer until the compiler MVP has adoption evidence

These may be valuable, but they should not consume the 0.17–0.20 critical path:

- #350, #407, #425, #435, #439, #444, #446, #533, #550, #551, #553, #556, #587, #612, #615, #636, #658, #663, #667, #719, #735, #737, #750, #751, #756.

Key rules:

- #350 and broader distribution work follow verified adapters, the killer demo, and credible claims;
- #444 waits until representative routing quality is fixed before 10k-scale optimization;
- #636 waits for evidence that the scorer is stable and materially improves outcomes;
- gateway operations/control-plane work remains maintenance-only during the transition;
- custom phases wait until the four canonical phases prove insufficient.

### Experiment — evidence required before roadmap promotion

These vertical packs, workbenches, and domain-specific agent experiences may be useful as experiments or downstream examples. They are not core roadmap commitments yet:

- #761, #762, #764, #765, #766, #769, #770, #775, #776, #777, #778, #781, #782, #783, #784, #786, #787, #788.

Promotion requires all of:

1. an identifiable external user/problem;
2. evidence that the capability belongs in ContextWeaver rather than an example or separate project;
3. a reusable normalized contract rather than domain-specific orchestration;
4. measurable improvement in task success, selection quality, or context quality;
5. no diversion from the compiler MVP.

### Close as not planned

- #628 — typed Python execution stubs conflict with the host-executes boundary and add an execution surface the core should not own;
- #631 — close after its useful result-safety requirements are referenced from #376 and the host/result-ingestion contracts.

## Canonical implementation tracks

The compiler epic should contain a compact set of tracks rather than dozens of policy micro-issues:

1. **Architecture and boundary** — ADR, public surface classification, legacy gateway freeze.
2. **Sources and adapters** — `CapabilitySourceAdapter`, `CapabilitySourceSnapshot`, multi-source failure/fallback rules, official adapter conformance.
3. **Bundle and reproducibility** — manifest, lock, content-addressed components, logical/binary identities, compatibility, migration, deterministic package.
4. **Resources and containment** — resource descriptors, closure validation, external resolver contract, integrity checks.
5. **Runtime** — `CompiledAgent`, route/hydrate/context APIs, host executor seam, normalized results.
6. **Evaluation** — routing gold sets, adversarial cases, real-model/task-success gates, ablations.
7. **Enrichment** — deterministic enrichers, optional LLM proposals, provenance, acceptance and evaluation gates.
8. **Minimal trust metadata** — recomputable `TrustSummary` and runtime assessment, without deployment-policy infrastructure.
9. **Demo and migration** — killer demo, tutorial, gateway migration, evidence-based claims.

## Adapter policy

Official support means **framework object/configuration → normalized capability snapshot → compiled bundle**. It does not mean ContextWeaver executes the framework.

Three tiers:

1. protocol/core-source adapters;
2. official framework adapters, experimental and maintained in the main repository behind lazy imports/extras;
3. community/long-tail plugins after the normalized contract stabilizes.

Each official adapter requires:

- SDK-free fixture and representative real-SDK fixture;
- deterministic conversion and bundle round-trip;
- provenance, non-secret runtime binding, and structured unsupported-field reporting;
- identity-collision and secret-leakage tests;
- declared minimum and tested/latest framework versions;
- periodic real-SDK CI, without allowing one experimental adapter to block unrelated core/security releases.

## Bundle and trust decisions

- canonical bundle is a diffable directory; deterministic ZIP is a transport;
- components are content-addressed and referenced by a manifest;
- required hydration closure is embedded or explicitly externalized, never silently omitted;
- the host resolves external resources; ContextWeaver performs no network access by default;
- `lock.json` pins semantic inputs and is updated only by an explicit lock-update flow;
- logical identity is separate from platform-derived binary indexes;
- `TrustSummary` is stored in the manifest as a recomputable projection;
- detailed evidence remains in separate content-addressed reports;
- runtime checks produce a separate `RuntimeTrustAssessment` and never mutate bundle identity;
- deployment authorization, online revocation, acknowledgement, and operational enforcement remain outside the MVP.

## Kill criteria

The compiler direction should be reconsidered if, after the 0.20 proof release:

- representative catalogs cannot be compiled reproducibly;
- routing quality and task success do not pass defined gates;
- phase-aware compilation produces no measurable context/task benefit;
- no external users demonstrate demand;
- the design remains materially dependent on one provider or framework.

## Backlog operating rules

- Every new issue must identify the user/problem, evidence, product track, and measurable acceptance criteria.
- New gateway execution/control-plane features are rejected unless required for security, correctness, compatibility, data-loss prevention, or migration.
- Vertical ideas start as experiments and do not receive core milestones without promotion evidence.
- Issues that duplicate a canonical track are absorbed rather than maintained as parallel epics.
- Claims and distribution follow reproducible evidence.
- Labels are normalized through #717; no new ad-hoc priority/status taxonomy should be introduced.
- Milestones represent the release sequence above, not arbitrary issue batches.

## Immediate next actions

1. Rewrite #758, #376, #408, #409, #433, #434, #477, #561, #610, and #651.
2. Close #628 and #631 as not planned/absorbed.
3. Apply accepted/deferred/experiment classifications through the repository's canonical label taxonomy once #717 defines it.
4. Start 0.17 with the architecture boundary, API surface classification, gateway freeze, and adapter lifecycle.
5. Create only the minimum missing implementation issues under #758 after checking the rewritten canonical issues for coverage.

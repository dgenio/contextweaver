# Compiler-first implementation map

This map turns the July 2026 backlog audit into a compact executable issue graph.

## Product epics

- #758 — offline capability compiler and phase-aware context runtime;
- #376 — legacy gateway freeze, migration, deprecation and extraction.

## 0.17 — boundary and stabilization

- #610 — classify and gate public APIs as core, experimental, legacy and internal-accidental;
- #651 — adapter tiers, compatibility windows, conformance and retirement;
- #717 — canonical GitHub label and roadmap-disposition taxonomy;
- #631 — cross-platform semantic reproducibility and profile-scoped byte identity;
- #749 — Windows compatibility;
- #752 — invariants;
- #753 — module-size policy;
- #754 — isolate adapter and core development dependencies.

Exit condition: the host-executes boundary, legacy surface and official adapter support policy are explicit and testable.

## 0.18 — compiled artifact MVP

### Source discovery and normalization

- #793 — `CapabilitySourceAdapter`, `CapabilitySourceSnapshot`, multi-source discovery, fallback and atomic lock update;
- #651 — adapter conformance/support governance;
- #506 — capability identity and alias collisions;
- #480 — untrusted catalog text handling.

### Bundle and reproducibility

- #794 — content-addressed bundle, manifest, lock, writer, loader, verifier and diff;
- #477 — logical/physical compatibility and migrations;
- #631 — cross-platform semantic reproducibility;
- #486 — version model-facing surfaces;
- #527 — fuzz loaders, bundle contracts, schemas and graphs;
- #586 — pinned context/configuration behavior.

### Resources and containment

- #795 — resource closure, containment and host-provided `ResourceResolver`;
- #478 — executable invariants;
- #613, #614, #617 and #619 — adjacent hardening and compatibility work retained by the audit.

### Enrichment

- #796 — deterministic enrichers, optional LLM proposals, provenance, acceptance and ablation gates;
- #489 — adversarial evaluation;
- #480 — model/catalog text sanitization.

### Minimal trust metadata

- #797 — recomputable `TrustSummary` and separate `RuntimeTrustAssessment`;
- #794 — storage and verification;
- #409 — candidate preview/report.

Exit condition: representative multi-source inputs produce a deterministic, inspectable, verifiable candidate bundle without executing capabilities.

## 0.19 — runtime and host seam

- #408 — `CompiledAgent.load(...)`, route, hydrate, context and result APIs;
- #453 — routing ownership/boundary;
- #499 — context contracts;
- #412 — phase budget semantics;
- #561 — host-provided outcome/evaluation ingestion;
- #641 — sync/async parity;
- #795 — external resource resolution;
- #797 — runtime restriction/trust assessment.

Exit condition: two host styles can use the same bundle and produce equivalent route/hydrate/result-reintegration behavior without ContextWeaver owning execution.

## 0.20 — evaluation and adoption proof

- #492 — routing gold set;
- #445 — representative real-model evaluation;
- #489 — adversarial evaluation;
- #440 — property tests;
- #434 — compiler-first killer demo and tutorial;
- #409 — pre-compilation analysis report;
- #397 — evidence-based claims;
- #433 — neutral ecosystem/landscape positioning.

Exit condition: the killer demo passes deterministic CI, representative routing/task gates are credible, and claims are linked to reproducible evidence.

## 0.21 — migration

- #376 — gateway transition and deprecation gates;
- #610 — legacy API inventory and replacements;
- #434 — migration examples;
- #433 — updated positioning and non-goals.

Exit condition: compiler-first documentation is the default, every legacy execution surface has a replacement/extract/remove decision, and at least one deprecation release has elapsed before removal.

## Dependency order

```text
#610 + #651 + #717
        ↓
#793 ──→ #795
  │        │
  └──→ #794 ←── #477 + #631
             │
#796 ────────┤
#797 ────────┤
             ↓
           #408
             ↓
#492 + #445 + #489 + #440
             ↓
        #409 + #434
             ↓
           #376
```

## Scope rule

No child issue may expand core ownership into capability execution, credentials, IAM, production orchestration, deployment enforcement or a general MCP control plane. Such requirements belong to the host or a separately evaluated integration/plugin.

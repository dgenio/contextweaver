# Label taxonomy

This repository uses a small, composable label scheme so humans and coding agents can find work without reading the whole backlog.

## Required dimensions

Apply at least one label from each dimension when triaging an issue.

### Area labels

| Label | Use for |
| --- | --- |
| `area/context` | Context packing, selection, firewall, memory, handoff, prompt assembly. |
| `area/routing` | Catalogs, graph building, route scoring, cards, manifests, hydration. |
| `area/gateway` | MCP gateway, proxy runtime, gateway policy, validation, diagnostics. |
| `area/adapters` | Framework/provider adapters and integration guides. |
| `area/docs` | Documentation, examples, recipes, onboarding. |
| `area/benchmarks` | Benchmarks, eval fixtures, scorecards, calibration. |

### Complexity labels

| Label | Use for |
| --- | --- |
| `complexity/xs` | One file, documentation or very small code change, no architectural decisions. |
| `complexity/s` | One focused module plus tests, clear acceptance criteria. |
| `complexity/m` | Cross-module work or non-trivial API/design considerations. |
| `complexity/l` | Larger project work; maintainers should break these down before external contribution. |

### Type labels

| Label | Use for |
| --- | --- |
| `bug` | Incorrect behavior or regression. |
| `enhancement` | New behavior in an existing area. |
| `documentation` | Docs-only or mostly-docs work. |
| `integration` | New adapter, provider, framework, or external interface support. |
| `performance` | Runtime, token, memory, benchmark, or scaling work. |
| `security` | Secret handling, sandboxing, validation, supply-chain, or data exposure risk. |
| `testing` | Test coverage, fixtures, CI checks, smoke tests. |
| `developer-experience` | Contributor tooling, templates, local workflow, maintainability. |

## Discovery labels

Use these labels to advertise work that is ready for contributors.

| Label | Criteria |
| --- | --- |
| `good first issue` | One file/module, explicit acceptance criteria, no deep architecture required, tests/docs path named. |
| `help wanted` | Maintainers want outside help and the work is scoped enough to start. |
| `agent-friendly` | Issue is deterministic enough for coding agents: exact files, acceptance criteria, and test commands are listed. |

## Suggested colors and descriptions

These are maintainer-facing suggestions for GitHub label creation.

| Label | Color | Description |
| --- | --- | --- |
| `area/context` | `1f77b4` | Context engine, firewall, memory, prompt building. |
| `area/routing` | `9467bd` | Router, catalogs, cards, graph, hydration. |
| `area/gateway` | `17becf` | MCP gateway/proxy runtime and diagnostics. |
| `area/adapters` | `ff7f0e` | Framework/provider adapters and integration surfaces. |
| `area/docs` | `2ca02c` | Documentation, examples, recipes, onboarding. |
| `area/benchmarks` | `8c564b` | Benchmarks, evals, scorecards, fixtures. |
| `complexity/xs` | `c7e9c0` | Very small, isolated change. |
| `complexity/s` | `a1d99b` | Focused small change with tests. |
| `complexity/m` | `74c476` | Medium scoped change. |
| `complexity/l` | `31a354` | Large or needs decomposition. |
| `agent-friendly` | `bfdadc` | Suitable for coding agents with clear files/tests. |
| `developer-experience` | `d4c5f9` | Contributor and maintainer workflow improvements. |
| `integration` | `fdae6b` | Adapter/provider/framework integration work. |
| `testing` | `fdd0a2` | Test coverage, fixtures, smoke checks. |
| `security` | `fb6a4a` | Security-sensitive behavior or hardening. |
| `performance` | `9ecae1` | Runtime/token/benchmark performance. |

## Triage checklist

1. Add one `area/*` label.
2. Add one `complexity/*` label.
3. Add one type label.
4. Add `good first issue` only if the issue names exact files/modules and has acceptance criteria.
5. Add `agent-friendly` only if a coding agent can validate the work locally without credentials or network access.
6. Prefer breaking `complexity/l` issues into smaller `complexity/xs` or `complexity/s` issues before adding `good first issue`.

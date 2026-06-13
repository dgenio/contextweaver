# Label taxonomy

This repository uses a small, predictable label scheme so humans and coding agents can find appropriate work quickly.

Maintainers can create or reconcile these labels in GitHub. Contributors should suggest labels in issue text when they do not have permission to apply them.

## Area labels

Use exactly one primary `area/*` label when possible; add a second only when the work genuinely spans boundaries.

| Label | Description | Suggested color |
| --- | --- | --- |
| `area/context` | Context compilation, firewalling, memory, facts, artifacts, redaction. | `1f77b4` |
| `area/routing` | Catalogs, cards, router scoring, graph building, hydration. | `9467bd` |
| `area/gateway` | MCP gateway/proxy, gateway server/runtime/policy/diagnostics. | `2ca02c` |
| `area/adapters` | Framework/provider adapters and integration glue. | `ff7f0e` |
| `area/docs` | Documentation, examples, recipes, contributor material. | `0075ca` |
| `area/benchmarks` | Benchmarks, fixtures, scorecards, evaluation data. | `8c564b` |

## Complexity labels

Use these to help contributors choose work with the right scope.

| Label | Description | Suggested color |
| --- | --- | --- |
| `complexity/xs` | One small file or documentation-only change; no architecture knowledge required. | `c2e0c6` |
| `complexity/s` | Bounded change in one module plus tests or docs. | `bfdadc` |
| `complexity/m` | Several files or an integration point; needs project familiarity. | `d4c5f9` |
| `complexity/l` | Cross-cutting design or migration; maintainer pairing recommended. | `f9d0c4` |

## Discovery labels

| Label | Description | Suggested color |
| --- | --- | --- |
| `good first issue` | Safe starter issue with explicit acceptance criteria and a narrow file/module scope. | `7057ff` |
| `help wanted` | Maintainers welcome external ownership; scope is ready enough for contributors. | `008672` |
| `agent-friendly` | Issue has enough context, acceptance criteria, and test commands for coding agents. | `5319e7` |

## Type labels

| Label | Description | Suggested color |
| --- | --- | --- |
| `bug` | Incorrect behavior or regression. | `d73a4a` |
| `enhancement` | New or improved capability. | `a2eeef` |
| `documentation` | Docs-only or primarily docs work. | `0075ca` |
| `integration` | Third-party framework/provider integration. | `fbca04` |
| `performance` | Speed, memory, token, or benchmark improvement. | `fef2c0` |
| `security` | Security boundary, secret handling, sandboxing, or disclosure-sensitive work. | `ee0701` |
| `testing` | Tests, fixtures, coverage, CI reliability. | `c5def5` |
| `developer-experience` | Contributor tooling, templates, local workflow, typing/lint ergonomics. | `bfd4f2` |

## Labeling rules of thumb

1. Every actionable issue should have at least: one `area/*`, one type label, and one complexity label.
2. Use `good first issue` only when the issue includes:
   - the likely file or module to edit,
   - explicit acceptance criteria,
   - a suggested test command,
   - no requirement to understand the full architecture.
3. Use `agent-friendly` when the issue includes enough context for an autonomous agent to make a safe local patch without asking follow-up questions.
4. Use `help wanted` when maintainers are comfortable with an external contributor driving the issue to completion.
5. Prefer splitting an issue over labeling it `complexity/l` if there is a clear starter-sized slice.

## Backlog triage checklist

For each open issue:

- Add one area label.
- Add one type label.
- Add one complexity label.
- Add `help wanted` if external contribution is welcome.
- Add `good first issue` only if it satisfies the starter criteria above.
- Add `agent-friendly` if the issue includes file pointers, acceptance criteria, and test commands.

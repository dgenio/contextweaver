# GitHub labels for contextweaver

> Labels live on GitHub, not in the repo. Maintainers create them through the
> GitHub UI or `gh label create`. This file is the authoritative reference for
> the label taxonomy actually in use and how each family is applied. Triagers
> and AI coding agents follow it when labelling issues and PRs — keep it in
> sync with the live labels.

The taxonomy has four prefixed families — `priority:`, `complexity:`, `area/`,
and `type:` — plus a set of unprefixed topic labels. Most open issues carry a
`priority:` and a `complexity:` label; an `area/` label where one applies.

## Priority — `priority:<level>`

| Label | When to apply |
|---|---|
| `priority:high` | Needed soon; blocks a milestone or other work. |
| `priority:medium` | Default for actionable backlog work. |
| `priority:low` | Worth doing, not time-sensitive. |

> **Known drift:** spaced variants (`priority: high`, `priority: medium`,
> `priority: low`) also exist on some issues. Treat the unspaced
> `priority:<level>` form as canonical and consolidate the spaced duplicates
> when relabelling.

## Complexity — `complexity:<size>`

| Label | When to apply |
|---|---|
| `complexity:simple` | Scoped to one file/module; little architectural context. |
| `complexity:average` | Spans a few files or needs moderate design judgement. |
| `complexity:complex` | Cross-cutting, architectural, or large surface area. |

## Area — `area/<layer>`

Maps an issue to the part of the tree it touches. Add a new `area/<name>` label
when a new module/surface needs one.

| Label | Scope |
|---|---|
| `area/context` | `src/contextweaver/context/` — pipeline, manager, firewall, views. |
| `area/routing` | `src/contextweaver/routing/` — catalog, graph, router, cards. |
| `area/adapters` | `src/contextweaver/adapters/` — MCP, A2A, providers, sidecar. |
| `area/gateway` | MCP gateway / proxy runtime and its meta-tools. |
| `area/cli` | `src/contextweaver/__main__.py` and CLI subcommands. |
| `area/observability` | Diagnostics, telemetry, status/health surfaces. |
| `area/benchmarks` | `benchmarks/` and the scorecard renderers. |
| `area/eval` | `contextweaver.eval` and evaluation harnesses. |
| `area/docs` | `docs/` and `README.md` (combine with `documentation`). |
| `area/store` | `src/contextweaver/store/` — EventLog, ArtifactStore, FactStore. |

## Type — `type:<kind>`

A lightweight work-kind family used alongside the long-standing GitHub
defaults (`bug`, `enhancement`, `documentation`).

| Label | When to apply |
|---|---|
| `type:feature` | New user-facing capability. |
| `type:task` | Maintenance / chore / process work with no new feature surface. |

## Topic labels (unprefixed)

Applied in addition to the prefixed families to describe the nature of the
work. Not mutually exclusive.

| Label | When to apply |
|---|---|
| `bug` | Reproducible defect. (GitHub default.) |
| `enhancement` | New feature or improvement. (GitHub default.) |
| `documentation` | README, docs/, examples/, or in-repo prose. (GitHub default.) |
| `architecture` | Touches structural/layering decisions. |
| `refactor` | Internal restructuring, no behaviour change. |
| `performance` | Latency / throughput / memory. |
| `reliability` | Robustness, error handling, durability. |
| `security` | Sensitivity, redaction, supply-chain, or auth. Treat private until triaged. |
| `breaking-change` | Affects the public API; needs a CHANGELOG migration note. |
| `testing` | Test coverage, harnesses, CI test surface. |
| `evals` | Quality-measurement / evaluation work. |
| `prompt-engineering` | Model-facing text surfaces (cards, plugin prompts). |
| `ai` | Optional LLM-assisted / model-backed feature. |
| `llm` | Direct LLM call paths. |
| `rag` | Retrieval-augmented / provenance work. |
| `provider-abstraction` | Provider-agnostic `call_fn`-style surfaces. |
| `integration` / `integrations` | Third-party framework / ecosystem integration. |
| `ecosystem` | Broader ecosystem positioning and interop. |
| `spec-compliance` | Conformance to an external spec (MCP, A2A, weaver-spec). |
| `investigation` | Scoping/spike issue; output is findings, not necessarily code. |
| `blocked` | Cannot proceed until a dependency lands. |
| `needs-info` | Awaiting clarification before it is actionable. |
| `roadmap` | Strategic, milestone-level direction. |
| `product` | Product-shaping / UX-of-the-library decisions. |
| `adoption` | Lowers the barrier to first successful use. |
| `developer-experience` | Improves the maintainer/contributor toolchain. |
| `contributor-experience` | Onboarding, governance, contribution flow. |
| `deprecation` | Removing or sunsetting a surface. |
| `good first issue` | Scoped, well-defined, claimable end-to-end in <2 hours. (GitHub default.) |
| `good-first-ai-issue` | Explicit acceptance criteria, small surface — suitable for an AI coding agent. |
| `help wanted` | Maintainers would welcome an external contributor. |

> **Known drift:** both `integration` and `integrations` exist. Prefer
> `integrations` (the more widely applied form) and fold `integration` into it
> when relabelling.

## Milestone — `milestone/<version>`

Applied to issues and PRs that gate a specific release. A milestone label
indicates the target version rather than a due-date of the work itself.

| Label | Target |
|---|---|
| `milestone/v0.2` | Core pipeline completion. |
| `milestone/v0.3` | Production readiness. |
| `milestone/v1.0` | Runtime modes + security. |
| `milestone/v0.4` | Observability & evaluation. |
| `milestone/v0.5` | Advanced routing & scale. |

## How to keep this file current

Run the following to dump **all** live labels and compare against this
file. The output includes unregistered labels that may need canonicalisation.

```bash
gh label list --limit 999 --json name,description \
  --jq '.[] | "| \(.name) | \(.description) |"'
```

## Creating labels on GitHub

Using `gh label create` (assumes a logged-in `gh` CLI). Colours are
suggestions; adjust to taste. `bug`, `enhancement`, `documentation`, and
`good first issue` are GitHub defaults — do not recreate them.

```bash
# Priority
gh label create "priority:high"          --color B60205
gh label create "priority:medium"        --color FBCA04
gh label create "priority:low"           --color 0E8A16

# Complexity
gh label create "complexity:simple"      --color C2E0C6
gh label create "complexity:average"     --color FBCA04
gh label create "complexity:complex"     --color D93F0B

# Area
gh label create "area/context"           --color C2E0C6
gh label create "area/routing"           --color BFDADC
gh label create "area/adapters"          --color FAD8C7
gh label create "area/gateway"           --color FAD8C7
gh label create "area/cli"               --color FEF2C0
gh label create "area/observability"     --color C5DEF5
gh label create "area/benchmarks"        --color D4C5F9
gh label create "area/eval"              --color D4C5F9
gh label create "area/docs"              --color 0075CA
gh label create "area/store"             --color C5DEF5

# Type
gh label create "type:feature"           --color A2EEEF
gh label create "type:task"              --color BFD4F2
```

## Issue templates that auto-apply labels

Templates under `.github/ISSUE_TEMPLATE/` set initial labels:

| Template | Auto-applied labels |
|---|---|
| `bug_report.yml` | `bug` |
| `feature_request.yml` | `enhancement` |
| `integration_request.yml` | `enhancement`, `integrations` |
| `docs_improvement.yml` | `documentation`, `area/docs` |

Triagers should add `priority:`, `complexity:`, `area/`, and any topic labels
manually after the template-applied defaults.

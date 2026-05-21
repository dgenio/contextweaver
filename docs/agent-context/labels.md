# Recommended GitHub labels for contextweaver

> Labels live on GitHub, not in the repo. Maintainers create these
> through the GitHub UI or `gh label create`. This file is the
> authoritative recommendation for the set the project ships with
> and how each one is used.

## Triage / status

| Label | Colour suggestion | When to apply |
|---|---|---|
| `needs triage` | `#fbca04` (yellow) | New issue not yet looked at; remove once labelled and assigned. |
| `needs reproduction` | `#fef2c0` (light yellow) | Bug report missing reproduction steps. |
| `confirmed` | `#0e8a16` (green) | Bug confirmed by a maintainer. |
| `wontfix` | `#ffffff` (white) | Out of scope; explain why in the closing comment. |
| `duplicate` | `#cfd3d7` (gray) | Closed as duplicate of another issue. |
| `stale` | `#d4c5f9` (light purple) | No activity for 60+ days; will auto-close if unrelated. |

## Type

| Label | Colour | When to apply |
|---|---|---|
| `bug` | `#d73a4a` (red) | Reproducible defect. Existing label. |
| `enhancement` | `#a2eeef` (light blue) | New feature or improvement. Existing label. |
| `documentation` | `#0075ca` (blue) | README, docs/, examples/, or in-repo prose. Existing label. |
| `performance` | `#fbca04` (yellow) | Latency / throughput / memory regressions or wins. |
| `security` | `#b60205` (dark red) | Sensitivity, redaction, supply-chain, or auth. Treat private until triaged. |
| `breaking` | `#ee0701` (red-orange) | Affects public API; needs CHANGELOG migration note. |

## Area

| Label | Colour | When to apply |
|---|---|---|
| `area:context` | `#c2e0c6` (light green) | `src/contextweaver/context/` — pipeline, manager, firewall, views. |
| `area:routing` | `#bfdadc` (teal) | `src/contextweaver/routing/` — catalog, graph, router, cards. |
| `area:adapters` | `#fad8c7` (light orange) | `src/contextweaver/adapters/` — MCP, A2A, FastMCP, providers. |
| `area:store` | `#c5def5` (light blue) | `src/contextweaver/store/` — EventLog, ArtifactStore, FactStore. |
| `area:cli` | `#fef2c0` (light yellow) | `src/contextweaver/__main__.py`. |
| `area:benchmarks` | `#d4c5f9` (light purple) | `benchmarks/` and `scripts/render_scorecard.py`. |
| `area:docs` | `#0075ca` (blue) | `docs/` and `README.md` (combine with `documentation`). |
| `area:integrations` | `#bfd4f2` (light blue) | `docs/integration_*.md`, integration tests, example scripts wrapping third-party SDKs. |

## Contributor / agent friendly

| Label | Colour | When to apply |
|---|---|---|
| `good first issue` | `#7057ff` (purple) | Existing label. Scoped, well-defined, no deep architectural context required. Each one should be claimable end-to-end in <2 hours. |
| `help wanted` | `#008672` (teal) | Maintainers would welcome an external contributor; might be larger than `good first issue`. |
| `agent-friendly` | `#5319e7` (deep purple) | Acceptance criteria are explicit, the surface area is small, no judgement calls are required. Suitable for Claude Code / Copilot Agent Mode / Codex. Add this in addition to `good first issue` where it applies. |
| `mentor available` | `#1d76db` (blue) | A maintainer has volunteered to guide the contributor in PR review. |

## Release

| Label | Colour | When to apply |
|---|---|---|
| `launch` | `#5319e7` (deep purple) | Required for the public-launch release. |
| `release-blocker` | `#b60205` (dark red) | Must land before the next tag. |
| `release-notes` | `#f9d0c4` (peach) | Worth surfacing prominently in CHANGELOG / release notes. |

## How to create these on GitHub

Using `gh label create` (assumes a logged-in `gh` CLI):

```bash
gh label create "needs triage"           --color FBCA04 --description "Not yet triaged"
gh label create "needs reproduction"     --color FEF2C0 --description "Bug report missing reproduction steps"
gh label create "confirmed"              --color 0E8A16 --description "Bug confirmed by a maintainer"
gh label create "performance"            --color FBCA04 --description "Latency / throughput / memory"
gh label create "security"               --color B60205 --description "Sensitivity, redaction, supply-chain, auth"
gh label create "breaking"               --color EE0701 --description "Affects public API"
gh label create "area:context"           --color C2E0C6
gh label create "area:routing"           --color BFDADC
gh label create "area:adapters"          --color FAD8C7
gh label create "area:store"             --color C5DEF5
gh label create "area:cli"               --color FEF2C0
gh label create "area:benchmarks"        --color D4C5F9
gh label create "area:docs"              --color 0075CA
gh label create "area:integrations"      --color BFD4F2
gh label create "help wanted"            --color 008672
gh label create "agent-friendly"         --color 5319E7 --description "Suitable for Claude Code / Copilot Agent / Codex"
gh label create "mentor available"       --color 1D76DB
gh label create "launch"                 --color 5319E7
gh label create "release-blocker"        --color B60205
gh label create "release-notes"          --color F9D0C4
gh label create "wontfix"                --color FFFFFF
gh label create "duplicate"              --color CFD3D7
gh label create "stale"                  --color D4C5F9
```

Existing labels (already present on the repo): `bug`, `enhancement`,
`documentation`, `good first issue`. Do not recreate them.

## Issue templates that auto-apply labels

Templates under `.github/ISSUE_TEMPLATE/` set initial labels:

| Template | Auto-applied labels |
|---|---|
| `bug_report.yml` | `bug` |
| `feature_request.yml` | `enhancement` |
| `integration_request.yml` | `enhancement`, `area:integrations` |
| `docs_improvement.yml` | `documentation`, `area:docs` |

Triagers should add area / contributor-friendly labels manually.

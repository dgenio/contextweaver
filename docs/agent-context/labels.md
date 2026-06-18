# GitHub labels for contextweaver

> Labels live on GitHub, not in the repo. This file documents the
> canonical label set. Use `gh label list` for the live reference.

## Triage / Status

| Label | Description |
|-------|------------|
| `bug` | Something isn't working. |
| `documentation` | Improvements or additions to documentation. |
| `duplicate` | This issue or pull request already exists. |
| `enhancement` | New feature or request. |
| `good first issue` | Good for newcomers. Scoped, well-defined, no deep architectural context required. |
| `help wanted` | Maintainers would welcome an external contributor. |
| `invalid` | This doesn't seem right. |
| `question` | Further information is requested. |
| `wontfix` | This will not be worked on. |

## Area

| Label | Scope |
|-------|-------|
| `area/adapters` | Protocol adapters: MCP, A2A. |
| `area/context` | Context engine: manager, pipeline, firewall. |
| `area/eval` | Evaluation, benchmarking, quality measurement. |
| `area/infra` | CI, builds, tooling, project infrastructure. |
| `area/observability` | Metrics, logging, debugging. |
| `area/routing` | Routing engine: catalog, graph, router, cards. |
| `area/store` | Data stores: event log, artifacts, episodic, facts. |

## Priority

| Label | Meaning |
|-------|---------|
| `priority/high` | High priority — closes a critical gap. |
| `priority/medium` | Medium priority — production readiness. |
| `priority/low` | Lower priority — scale & validation. |

## Complexity

| Label | Meaning |
|-------|---------|
| `complexity/simple` | Straightforward change, minimal risk. |
| `complexity/average` | Standard effort, moderate familiarity needed. |
| `complexity/complex` | Cross-cutting, significant design or risk. |

## Milestone

| Label | Target |
|-------|--------|
| `milestone/v0.2` | Core pipeline completion. |
| `milestone/v0.3` | Production readiness. |
| `milestone/v1.0` | Runtime modes + security. |
| `milestone/v0.4` | Observability & evaluation. |
| `milestone/v0.5` | Advanced routing & scale. |

## Other

| Label | Description |
|-------|-------------|
| `examples` | Example scripts and demo improvements. |
| `security` | Security-related: data leakage, access control, redaction. |
| `spike` | Time-boxed research or proof-of-concept. |

## Note on issue-only labels

Issues may carry labels not registered in the repo's label list
(e.g. `type:feature`, `type:task`, `priority: high` with colon
instead of slash, `good-first-ai-issue`, `blocked`, `needs-info`,
`reliability`, `developer-experience`). These are not canonical;
consider registering them via `gh label create` if they see
frequent use.

## How to keep this file current

```bash
# Dump live labels and regenerate the tables above
gh label list --json name,description --jq '.[] | "| \(.name) | \(.description) |"'
```

Compare output against this file periodically (e.g. every quarter)
or when a new label family is introduced.

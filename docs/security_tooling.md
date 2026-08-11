# Security Tooling & Exception Process

This page documents the automated security tooling that runs in CI, how
findings are triaged, and the process for filing and recording an **exception**
when a finding is a false positive or an accepted risk. It is the runbook
referenced from [`SECURITY.md`](https://github.com/dgenio/contextweaver/blob/main/SECURITY.md)
and from the security workflows themselves (issue #692, umbrella #443).

The goal is that scanner noise never silently erodes the gate: every
suppression is explicit, attributed, time-bounded, and linked to a tracking
issue.

## Tooling overview

| Tool | Workflow | Gating? | Surfaces findings in |
|------|----------|---------|----------------------|
| CodeQL | `.github/workflows/codeql.yml` | No (advisory — alerts surface in the Security tab; they do not fail the PR check by default) | Security tab → Code scanning |
| OpenSSF Scorecard | `.github/workflows/ossf-scorecard.yml` | No (informational) | Security tab + README badge |
| pip-audit (core) | `.github/workflows/pip-audit.yml` | **Yes** | Workflow logs / job summary |
| pip-audit (dev extra) | `.github/workflows/pip-audit.yml` | No (report-only) | Workflow logs / job summary |
| Dependabot | `.github/dependabot.yml` | No (opens PRs) | Pull requests |
| Release integrity | `.github/workflows/publish.yml` | **Yes** (blocks publish) | Release run logs |

The **core** dependency set is gating because contextweaver is designed to sit
in the data path between agents and tools. The **dev/test** extra pulls a large
transitive tree (`crewai`, `mem0ai`, `fastmcp`, `langgraph`, `langchain-core`)
and is report-only so the gate stays signal-rich.

## Dependency update policy

contextweaver is a **library**, so `pyproject.toml` dependency ranges are
compatibility contracts rather than an application deployment snapshot.
Declared `>=` floors are only raised when the floor-deps proof demonstrates
that the old minimum is no longer truthful. They are not raised merely because
a newer release exists.

The repository intentionally does **not** commit an application-style Python
lockfile. Normal CI resolves current stable dependencies, the floor-deps job
proves the declared minimum direct versions, and
`.github/workflows/deps-latest-weekly.yml` exercises the newest releases and
pre-releases as an early-warning surface. A committed `uv.lock` would otherwise
turn transitive development-resolution churn into PRs without representing the
environment that users install from the published wheel.

Dependabot therefore follows these rules:

- routine Python minor/patch version updates are grouped into one weekly PR;
- ordinary version updates have a short cooldown to avoid opening PRs for
  releases that are immediately superseded; security updates are not delayed by
  that cooldown;
- security updates have explicit grouping rules rather than inheriting the
  version-update grouping accidentally;
- the intentional `mcp<2` ceiling is not widened automatically: MCP SDK v2 is a
  source/API and protocol migration and is tracked as maintainer work;
- Docker Actions are grouped so related runtime/toolkit majors move together;
- every GitHub Action reference is pinned to an immutable commit SHA, with a
  version comment for readability. Dependabot maintains those SHA pins.

## Ownership and SLA

- **Owner:** the repository maintainers (see `CODEOWNERS`/`GOVERNANCE` once
  published; until then, `@dgenio`).
- **Triage SLA:** new gating findings are triaged within **7 days**, matching
  the vulnerability-report triage target in `SECURITY.md`.
- **Fix SLA:** critical/high within **30 days**; medium/low best-effort on the
  next scheduled release.

## Triage workflow

1. **Confirm.** Reproduce the finding from the workflow logs or the Security
   tab. Note the advisory id (e.g. `GHSA-xxxx` / `PYSEC-xxxx`) or the CodeQL
   rule id.
2. **Classify.** True positive, false positive, or accepted risk.
3. **Act:**
   - *True positive* → open a fix (bump the dependency, patch the code) and
     reference the advisory in the PR.
   - *False positive / accepted risk* → file an exception (below).

## Filing an exception

Do **not** broaden a gate or delete a check to silence a finding. Instead:

### pip-audit (dependency advisories)

Add an explicit, commented ignore in `.github/workflows/pip-audit.yml`:

```yaml
- name: Audit installed environment (gating)
  run: |
    pip-audit --progress-spinner off \
      --ignore-vuln GHSA-xxxx-xxxx-xxxx   # <reason>; tracked in #<issue>
```

Each `--ignore-vuln` entry **must** carry an inline comment with the reason and
a tracking issue. Entries are reviewed whenever the dependency changes and
removed once the advisory no longer applies.

### CodeQL (code scanning alerts)

Prefer fixing the code. When a finding is a genuine false positive, dismiss the
alert in the Security tab with reason **"False positive"** or **"Used in
tests"** and a justification comment. For a systemic pattern, narrow it with a
committed CodeQL config rather than dismissing alerts one by one.

### OpenSSF Scorecard

Scorecard is informational. Address the cheap, high-value checks first
(token-minimal workflow permissions, branch protection, pinned dependencies).
Document any check that is intentionally not satisfied here with a short
rationale.

## Exception register

Record active exceptions here so they are visible in one place and can be
audited. Empty until the first exception is filed.

| Date | Tool | Finding id | Reason | Tracking issue | Review by |
|------|------|------------|--------|----------------|-----------|
| —    | —    | —          | —      | —              | —         |

## OpenSSF Best Practices badge

Applying for the [OpenSSF Best Practices badge](https://www.bestpractices.dev/)
is a tracked manual step (issue #552): register the project, complete the
questionnaire, then add the awarded badge to `README.md`. The automated
**Scorecard** badge already surfaces continuously from the workflow above.

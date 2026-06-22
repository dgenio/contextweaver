# Security Policy

## Supported Versions

Only the latest patch release in the current minor series receives security updates.

| Version | Supported |
|---------|-----------|
| 0.16.x  | Yes       |
| < 0.16  | No        |

> This table is kept honest by `scripts/check_security_policy.py`, a gating CI
> check that fails when the supported series drifts from the package version in
> `pyproject.toml` or when a relative link below stops resolving.

For adopter-facing deployment boundaries, data flow, artifact exposure, and
hardening guidance, see the
[MCP Gateway Security Model](docs/security_model.md). This policy remains the
canonical vulnerability-reporting channel.

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Report vulnerabilities privately via
[GitHub Security Advisories](https://github.com/dgenio/contextweaver/security/advisories/new).
This opens a private, structured channel visible only to maintainers.

Include as much detail as possible:
- Affected version(s) and component(s)
- Steps to reproduce or a proof-of-concept
- Potential impact and suggested severity

## Response Timeline

| Milestone | Target |
|-----------|--------|
| Acknowledgment | Within 72 hours of report |
| Triage and severity assessment | Within 7 days |
| Fix for critical/high issues | Within 30 days |
| Fix for medium/low issues | Best-effort, next scheduled release |

We will keep you updated throughout the process and credit reporters in the
release notes (unless you prefer to remain anonymous).

## Scope

The following areas are **in scope** for security reports:

- **Context firewall bypasses** — raw tool output leaking into the prompt
  (`src/contextweaver/context/firewall.py`)
- **Prompt injection via tool output processing** — crafted tool results that
  manipulate the assembled context, bypass sensitivity filters, or defeat
  sensitivity enforcement (`src/contextweaver/context/sensitivity.py`)
- **Adapter input validation** — malformed or malicious data accepted by the
  MCP or A2A adapters (`src/contextweaver/adapters/`)
- **Deserialization vulnerabilities** — unsafe behavior in `to_dict` / `from_dict`
  or JSON loading paths that could lead to code execution or data corruption

## Out of Scope

The following are **not** in scope:

- **LLM behavior** — how a language model interprets or acts on the context
  that contextweaver assembles is outside our control and not our responsibility
- **Tool execution** — contextweaver prepares context and routes tools; it never
  executes tools or makes model calls. Runtime security is the host application's
  responsibility
- **Denial-of-service via large inputs** — unless exploitable beyond resource
  exhaustion (i.e. leads to data leakage)
- **Issues in dependencies** — report these to the upstream project directly;
  we will update affected dependencies promptly when notified

## Automated Security Tooling

The project runs continuous supply-chain and code-scanning automation:

- **CodeQL** (`.github/workflows/codeql.yml`) — static analysis on every PR,
  on `main`, and weekly.
- **OpenSSF Scorecard** (`.github/workflows/ossf-scorecard.yml`) — supply-chain
  health checks; results publish to the code-scanning dashboard and the README
  badge.
- **pip-audit** (`.github/workflows/pip-audit.yml`) — dependency vulnerability
  scanning (gating on core dependencies, report-only for the dev extra).
- **Dependabot** (`.github/dependabot.yml`) — weekly `pip` and `github-actions`
  updates.
- **Release integrity** (`.github/workflows/publish.yml`) — tag↔version gate,
  pre-publish tests, and signed build-provenance attestations on every release.

How findings are triaged, and how to file and document an exception for a false
positive, is described in the
[security tooling runbook](docs/security_tooling.md).

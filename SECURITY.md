# Security Policy

## Supported Versions

Only the latest patch release in the current minor series receives security updates.

| Version | Supported |
|---------|-----------|
| 0.1.x   | ✅ Yes    |
| < 0.1   | ❌ No     |

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
  manipulate the assembled context or bypass sensitivity filters
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
  exhaustion (e.g. leads to data leakage)
- **Issues in dependencies** — report these to the upstream project directly;
  we will update affected dependencies promptly when notified

# MCP Gateway Security Guide

A local MCP gateway launches upstream servers that may receive secrets through
environment variables and expose tools that read files, modify repositories,
call APIs, or delete resources. This guide is the task-oriented companion to the
[Security Model](security_model.md): how to run contextweaver in front of
powerful MCP servers with least privilege.

For the conceptual trust boundaries and non-goals, read the
[Security Model](security_model.md) first. For tuning the sensitivity/redaction
subsystem, see the [Sensitivity & Redaction guide](sensitivity.md).

## Threats this addresses

- Secrets passed to upstream servers via environment variables.
- Destructive tools exposed to the model by default.
- Workspace filesystem access broader than intended.
- Confusion between read-only and write-capable tools.
- Raw tool output (with embedded secrets) reachable via `tool_view`.
- Secrets leaking into diagnostics or error messages.

## Start secure by default

`contextweaver mcp serve` is secure by default (issue #744): it classifies and
scrubs secret/PII-shaped content in tool output before it reaches the prompt.
Keep it that way — only pass `--no-redact` when you have a specific reason, and
note it prints a startup warning when you do.

```bash
contextweaver mcp serve --config gateway.yaml   # secure by default
```

## Least privilege: deny destructive tools before they can be called

The runtime [policy gate](security_model.md#runtime-authorization-the-policy-gate)
is the enforcement point. Prefer a **default-deny allowlist** for untrusted or
high-blast-radius estates, and require approval for destructive operations:

```yaml
# gateway.yaml
catalog: ./catalog.json
redact: true          # explicit; this is also the default
policy:
  default: deny       # allowlist posture — nothing runs unless matched
  rules:
    - { namespace: github, tool: "issues.*", action: allow }
    - { namespace: github, tool: "pull_requests.*", action: allow }
    - { namespace: filesystem, tool: "read_*", action: allow }
    - { tags: [destructive], action: require_approval }
    - { tool: "*delete*", action: deny }
    - { meta_tool: tool_view, namespace: secrets, action: deny }
```

`deny` means the upstream tool is never invoked; `require_approval` returns an
`AUTH_REQUIRED` error a host can surface for human sign-off. This is enforced by
contextweaver regardless of whether the upstream server implements its own
controls — do not rely on MCP `readOnlyHint`/`destructiveHint` annotations as
policy; they are untrusted hints.

## Keep secrets out of committed config

Upstream servers often need tokens (e.g. a GitHub PAT). Never commit them.
Reference environment variables from the client/launcher and keep the values in
your shell or a secret manager:

```jsonc
// .vscode/mcp.json — the token lives in the environment, not the file
{
  "servers": {
    "github": {
      "type": "stdio",
      "command": "docker",
      "args": ["run", "-i", "--rm", "-e", "GITHUB_PERSONAL_ACCESS_TOKEN", "ghcr.io/github/github-mcp-server"]
    }
  }
}
```

Add gateway configs, persistent state directories (`--state-dir`), and artifact
directories to `.gitignore` when they can contain machine paths or credentials.

## Bound raw egress

`tool_view` re-exposes raw artifact bytes and is the intentional
raw-recovery surface. Gateway artifacts are stored **unredacted at rest** — the
scrubbing applies to prompt-bound summaries and cards, not the raw bytes. For
sensitive estates, deny or approval-gate raw egress with a `meta_tool: tool_view`
policy rule (see the example above) rather than assuming artifacts are scrubbed.

## Diagnostics do not print secret values

The built-in JSONL diagnostics stream (`mcp serve --diagnostics FILE`) records
canonical tool/artifact handles, namespaces, argument *key names*, sizes,
timings, and error codes — **not** query text, argument values, result text, or
artifact bytes. Upstream exception text (which can carry hostnames, paths, or
tokens) is control-character-stripped and length-capped before it reaches
model-visible context; the full detail stays operator-side in logs. Still,
treat the diagnostics file as operationally sensitive: restrict its permissions
and define a retention policy. Use `--quiet` only to suppress lifecycle chatter,
not as an access-control substitute.

## The HTTP sidecar

`contextweaver serve-api` is a thinner surface. Its `/v1/compact` endpoint can
scrub secrets end-to-end (issue #745): set `redact_secrets: true` in the sidecar
config to force it on for every request, or let a client opt in per request with
`"redact_secrets": true` in the `/v1/compact` body. It is off by default (posture
owned by #744). An unauthenticated bind still exposes the surface to any local
caller — set `--api-key` (or `CONTEXTWEAVER_SIDECAR_API_KEY`), bind to a trusted
interface, and avoid sending secret-bearing payloads over an untrusted network.
Binding without a key prints a startup warning.

## Checklist

- [ ] Serve with redaction on (default); justify any `--no-redact`.
- [ ] Use a `default: deny` policy for untrusted estates; `require_approval` for
      destructive tools; `deny` `*delete*`-style tools.
- [ ] Deny or approval-gate `tool_view` for sensitive namespaces.
- [ ] Keep tokens in the environment/secret manager, never in committed config.
- [ ] `.gitignore` gateway config, `--state-dir`, and artifact directories.
- [ ] Restrict permissions/retention on the diagnostics file.
- [ ] Authenticate the sidecar (`--api-key`) and keep it off untrusted networks.
- [ ] Remember: authorization and side-effect execution still ultimately rest
      with the upstream server and host — the gateway narrows and gates, it does
      not replace them.

# MCP Gateway Security Model

contextweaver is a local context-compilation and MCP gateway layer. It does
not call an LLM or implement upstream tool side effects, but it can process
tool schemas, tool results, session history, and raw artifacts that contain
sensitive data. Treat its artifact store and diagnostics as sensitive as the
upstream outputs they summarize.

This page describes deployment boundaries. Vulnerability reporting remains in
the repository's
[`SECURITY.md`](https://github.com/dgenio/contextweaver/blob/main/SECURITY.md).

For a task-oriented walkthrough of running the gateway in front of powerful
MCP servers (secrets, destructive tools, least privilege), see
[MCP Gateway Security Guide](security_mcp_gateway.md). For configuring the
sensitivity/redaction subsystem, see the
[Sensitivity & Redaction guide](sensitivity.md).

## Default security posture

The serving entrypoints are **secure by default** (issue #744). `contextweaver
mcp serve` runs with the deterministic
[`HeuristicSensitivityClassifier`](sensitivity.md) and secret scrubbing
(`redact_secrets`) enabled, so unlabelled tool output that carries
credential-shaped or PII-shaped content is classified and scrubbed before it
reaches a prompt-bound summary or a `ChoiceCard`. This is a deliberate
divergence from the library-level `ContextManager`, whose defaults stay
permissive for embedding in existing pipelines — the hardening is applied at
the gateway boundary, where an operator reasonably expects the firewall's
headline protections to be on.

Turn the protections off with `contextweaver mcp serve --no-redact` (or
`redact: false` in the config file). Doing so prints a one-line startup
**warning** to stderr, so an unprotected posture is always a visible choice,
never a silent default.

The HTTP sidecar (`contextweaver serve-api`) is a thinner surface: its
`/v1/compact` endpoint does **not** yet scrub secrets end-to-end (tracked in
issue #745). Binding it without `--api-key` prints a startup warning; do not
send secret-bearing payloads to an unauthenticated sidecar over an untrusted
network.

This decision is recorded here so it is not "simplified" away: the serving
defaults must stay secure-by-default, and any change that weakens them is a
security-relevant change requiring review.

## Runtime authorization: the policy gate

Reducing a large MCP surface to `tool_browse` / `tool_execute` / `tool_view`
moves the practical safety boundary into the gateway. contextweaver therefore
provides an explicit, deterministic **policy gate** evaluated before any
upstream dispatch and before any raw artifact egress (issue #373).

A `ToolPolicy` is an ordered list of match → action rules with a default
action. Each rule may match on the upstream `namespace`, a case-sensitive glob
over the tool id/name (`tool`), catalog `tags`, `read_only`, and the surface
(`meta_tool`: `tool_execute` or `tool_view`). The first matching rule wins;
otherwise the `default` applies. Actions are:

- `allow` — proceed normally.
- `deny` — return a `POLICY_DENIED` `GatewayError`; the upstream tool is
  **never** called and no raw content is returned.
- `require_approval` — return an `AUTH_REQUIRED` `GatewayError` (with
  `details.approval = "required"`) so a host or custom loop can surface it for
  human sign-off.

The default (`ToolPolicy()` / no policy configured) allows everything, so
existing deployments are unchanged; opt into `deny` / `require_approval` rules,
or set `default: deny` for an allowlist posture. Configure it under the
`policy` key of `mcp serve --config`:

```yaml
policy:
  default: allow
  rules:
    - { namespace: github, tool: "issues.*", action: allow }
    - { tags: [destructive], action: require_approval }
    - { tool: "*delete*", action: deny }
    - { meta_tool: tool_view, namespace: secrets, action: deny }  # block raw egress
```

MCP annotations such as `readOnlyHint` / `destructiveHint` are untrusted hints
and are **not** the enforcement mechanism — the policy is. Annotations may
inform a rule's authoring, but the gate decides.

## Data flow

```text
                         schemas / calls
MCP client  <------>  contextweaver gateway  <------>  upstream MCP server
    |                    |          |
    |                    |          +--> tool execution and authorization
    |                    |               remain upstream / host concerns
    |                    |
    |                    +--> artifact store
    |                         raw tool bytes, local and out-of-band by default
    |
    +--> model provider
         bounded ChoiceCards, summaries, selected artifact slices
```

Prompt-visible by default:

- Compact `ChoiceCard` fields, without full input schemas.
- Firewalled summaries and extracted facts.
- Artifact handles and metadata needed for progressive disclosure.
- A selected slice returned by `tool_view` after the client requests it.

Out-of-band by default:

- Raw text, resource, image, and audio bytes stored as artifacts.
- Full schemas until the selected tool is hydrated for execution.
- Context items excluded by budget or sensitivity policy.

Out-of-band does not mean harmless or encrypted. The default gateway uses an
in-memory artifact store in the gateway process. Other adapters can use
filesystem or caller-supplied stores.

## Data contextweaver can touch

- MCP tool names, descriptions, annotations, and input schemas.
- MCP text, structured content, resources, images, and audio handled by the
  adapter.
- Session messages and tool history ingested through provider adapters.
- Artifact bytes, labels, media types, sizes, and handles.
- Catalog and gateway configuration files.
- Routing queries, candidate identifiers, scores, and build statistics.
- OpenTelemetry attributes and metrics when the optional integration is
  enabled.
- Local gateway JSONL diagnostics when `mcp serve --diagnostics FILE` is
  enabled.

MCP annotations such as `readOnlyHint` are untrusted metadata. They may improve
presentation or routing, but must not grant permission or bypass approval.

## Network and egress

The context and routing algorithms do not make model calls and do not require
network access. Data can still leave the machine through surrounding systems:

- The MCP client sends the compiled prompt and tool responses to its configured
  model provider.
- Upstream MCP servers may call remote APIs or databases.
- An enabled OpenTelemetry exporter sends spans and metrics to its configured
  endpoint.
- Package installation requires a package index unless artifacts are already
  cached or installed.
- An explicitly selected token estimator may fetch tokenizer data on first use
  when its cache is cold; the documented fallback remains deterministic.

The default OpenTelemetry emission excludes raw queries, full tool
descriptions, schemas, and prompt content. Enabling
`otel_emit_experimental=True` can add sensitive content and should be limited
to a trusted, access-controlled backend.

The built-in gateway JSONL stream excludes query text, argument values, result
text, prompt text, and artifact bytes. It does include canonical tool and
artifact handles, namespaces, argument key names, sizes, timings, and error
codes. Treat those identifiers as operationally sensitive and restrict file
permissions and retention accordingly.

## Trust boundaries

### Host MCP client

The host decides which model receives context, which MCP server is available,
and whether a user must approve a call. contextweaver does not replace those
controls.

### contextweaver gateway

The gateway narrows discovery, validates selected arguments against the
hydrated schema, dispatches calls to an `UpstreamCall`, and firewalls returned
content. Routing is relevance selection, not authorization.

### Upstream MCP server

The upstream server implements the operation and its side effects. It must
authenticate callers, authorize access, validate business rules, and protect
its own credentials. contextweaver does not verify that a tool described as
read-only is actually read-only.

### Artifact store

The artifact store contains the bytes deliberately kept out of the prompt.
Anyone with process or storage access may be able to read them. A handle is an
address, not a capability token.

## Context firewall limits

The context firewall reduces prompt exposure and token use. It is not a data
loss prevention system or security sandbox.

- Raw bytes can remain in the artifact store after a summary is rendered.
- Summaries and extracted facts can still contain sensitive values unless a
  sensitivity or redaction policy removes them.
- `tool_view` deliberately re-exposes selected artifact content to the MCP
  client and therefore potentially to the model provider. It is the intentional
  raw-recovery surface, governed by the same `ToolPolicy` as `tool_execute`
  (issue #746): a `meta_tool: tool_view` rule can `deny` or `require_approval`
  raw egress per namespace/tool. Note that gateway artifacts are stored
  unredacted **at rest** (the scrubbing applies to prompt-bound summaries and
  cards, not the raw bytes), so the policy gate — not the firewall — is what
  bounds raw egress; attribution is best-effort for handles that do not encode
  a tool id.
- The current in-memory gateway store has no TTL, total-size quota, or
  per-handle authorization policy.
- Current selectors accept caller-provided ranges; deployments should use
  narrow selectors and should not assume a built-in maximum response size.
- The gateway does not neutralize prompt injection contained in a tool result.
  The host prompt and execution policy must treat tool content as untrusted.

A policy gate for `tool_view` egress now exists (issue #746, above). Artifact
TTLs, size limits, bounded-view selectors, store-time redaction, and
provenance remain tracked in
[#375](https://github.com/dgenio/contextweaver/issues/375). Until those ship,
high-sensitivity deployments should deny raw `tool_view` via policy (or in
their host integration) rather than relying on artifacts being scrubbed at
rest — they are not.

## Non-goals

contextweaver does not:

- Authenticate users or authorize tool execution.
- Call the model or control how it follows instructions.
- Sandbox or attest upstream MCP server processes.
- Guarantee that MCP annotations are truthful.
- Replace secret scanning, DLP, endpoint security, or storage encryption.
- Prevent a malicious or compromised upstream from returning prompt-injection
  content.
- Make an unsafe tool safe merely because it was selected by the router.

## Hardening checklist

- Use upstream MCP servers you trust and keep them patched.
- Register a tool either directly or behind the gateway, not both.
- Enforce user identity, authorization, and side-effect approval in the host or
  upstream server.
- Keep gateway configs, local state, and artifact directories out of version
  control when they contain machine paths or credentials.
- Put secrets in the client's environment/secret facility, not in committed
  JSON or YAML.
- Use sensitivity labels and redaction before prompt rendering; add
  store-before-view redaction when handling regulated data.
- Prefer `json_keys`, short line ranges, or a small `head` selector over whole
  artifact retrieval.
- Restrict filesystem permissions and process access around persistent
  artifact stores.
- Leave experimental OTel content emission disabled unless the exporter is
  trusted for the data class involved.
- Store gateway JSONL diagnostics in an access-controlled path and define a
  retention policy; use `--quiet` only to suppress lifecycle stderr, not as a
  substitute for diagnostics access control.
- Review tool descriptions and results as untrusted input; do not rely on
  `readOnlyHint`, `destructiveHint`, or similar annotations as policy.
- Pin the contextweaver version in managed deployments and review release notes
  before upgrading.

## Deployment questions

Before exposing a gateway to users, answer:

1. Which identities may use each upstream tool?
2. Where are raw artifacts stored, and who can read that location or process?
3. How long may artifacts remain available?
4. Which content is allowed to reach the model provider?
5. Who approves destructive or externally visible calls?
6. Where do OTel spans go, and can that backend hold the same data class?
7. What is the incident path if a summary or view exposes a secret?

If these answers are undefined, the deployment is not made safe by adding a
context firewall.

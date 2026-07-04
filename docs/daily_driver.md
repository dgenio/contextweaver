# Daily Driver Guide

Use contextweaver as a pressure-relief layer for tool-heavy sessions, not as
the default path for every chat message.

```text
User / IDE chat
  |
  +-- trivial question, tiny tool set, small result --> normal host-agent path
  |
  +-- large catalog, large result, long history ----> contextweaver gateway
                                                        |
                                                        +-- tool_browse
                                                        +-- tool_execute
                                                        +-- tool_view (only as needed)
```

contextweaver prepares bounded tool choices and compact result summaries. The
host application still owns the model call, authorization, user approval, and
execution policy. Upstream MCP servers remain the executors of record.

## Recommended daily loop

1. Start with the host client's normal chat path.
2. Use the gateway when the catalog is difficult to navigate, a result is too
   large for the prompt, or the active history needs deterministic budgeting.
3. Ask the client to call `tool_browse` with a routing-oriented query.
4. Execute only the selected `tool_id` through `tool_execute`.
5. Use `tool_view` for a narrow slice only when the summary is insufficient.
6. Inspect the route explanation, build statistics, and artifact reference
   before increasing budgets or exposing more data.

The gateway should usually replace duplicate direct registrations of the same
upstream tools. Advertising both the raw servers and the gateway gives the
model two competing paths and defeats the bounded-tool benefit.

## Start the gateway

The fastest trial requires no persistent installation:

```bash
uvx contextweaver mcp serve \
  --config examples/recipes/gateway_config.yaml \
  --dry-run
```

For regular use, install the CLI once:

```bash
pip install contextweaver
contextweaver mcp serve --config /path/to/gateway.yaml --dry-run
```

Enable local, payload-safe diagnostics in a directory that already exists:

```bash
contextweaver mcp serve \
  --config /path/to/gateway.yaml \
  --diagnostics /path/to/logs/contextweaver.jsonl \
  --quiet
```

Inspect the static catalog before launch and aggregate the event stream later:

```bash
contextweaver mcp inspect --catalog /path/to/catalog.yaml
contextweaver mcp stats --events /path/to/logs/contextweaver.jsonl
```

For support or incident triage, create a bounded local bundle:

```bash
contextweaver mcp incident-pack \
  --config /path/to/gateway.yaml \
  --diagnostics /path/to/logs/contextweaver.jsonl \
  --out /path/to/contextweaver-incident.zip
```

The pack includes a machine-readable manifest, environment summary, redacted
config/catalog excerpts, diagnostics summaries, redacted diagnostics, and a
reproduction checklist. It never reads shell history automatically; pass
`--command-log /path/to/commands.txt` only when you captured a command log
explicitly for that incident.

The packaged CLI still represents one configured static catalog source. Its
catalog report groups tools by namespace; it does not claim live health for
multiple upstream MCP processes.

`pipx run contextweaver ...` is another isolated option. The first `uvx` or
`pipx run` launch resolves a temporary environment and is slower than later
runs. Pin a deployment when reproducibility matters:

```bash
uvx contextweaver@0.14.0 mcp serve --config /path/to/gateway.yaml
pipx run --spec contextweaver==0.14.0 contextweaver mcp serve \
  --config /path/to/gateway.yaml
```

The packaged `mcp serve` command currently loads a static catalog and uses the
stub upstream handler for deterministic local exercise. For live upstream MCP
execution, compose `McpClientUpstream` or `MultiplexUpstream` in Python; see
[MCP Integration](integration_mcp.md#connecting-to-real-upstream-mcp-servers).

## Client instruction

Give the host agent a short operational rule. The same rule works in Cursor,
Claude Desktop, Claude Code, VS Code Copilot agent mode, and generic MCP
clients:

```text
Use the contextweaver MCP gateway when you need to browse or call tools from a
large catalog. Call tool_browse first with a routing-oriented query, execute
only the selected tool_id through tool_execute, and use tool_view only when the
summary is insufficient. Prefer narrow tool_view selectors. The gateway does
not grant authorization; follow the host application's approval and execution
policy.
```

Client-specific placement:

| Client | Where to put the rule |
|---|---|
| Cursor | Project rules or the task prompt |
| Claude Desktop | Project/custom instructions |
| Claude Code | `CLAUDE.md` or the current task prompt |
| GitHub Copilot | `.github/copilot-instructions.md` or repository instructions |
| Generic MCP client | System/developer prompt owned by the host application |

## Use contextweaver when

- The client sees dozens or hundreds of MCP, FastMCP, or Python tools.
- Tool results include large JSON objects, logs, tables, CSV, resources, or
  binary content.
- Multi-turn tool sessions accumulate more history than should reach every
  phase.
- You need deterministic prompt budgets and an inspectable record of what was
  included, dropped, or deduplicated.
- You want schemas hidden until a tool has been selected and hydrated.

For catalogs above roughly 300 tools, treat metadata quality as part of the
deployment: capture/import the upstream `tools/list`, normalize names and
descriptions, then validate routing against representative queries. The
current static-catalog workflow is documented in the
[MCP Context Gateway architecture](architectures/mcp_context_gateway.md).

## Do not use it when

- The agent has only three to five small tools.
- The interaction is one-shot Q&A with no tool or history pressure.
- Tool outputs are already small and the prompt comfortably fits its budget.
- The actual problem is pure retrieval, long-term memory, or observability.
- The host application has not defined who may invoke tools or approve side
  effects.
- You expect contextweaver to be an agent supervisor, model runtime, sandbox,
  or authorization service.

## Debug loop

When a route or prompt looks wrong, inspect these in order:

1. **Gateway configuration.** Confirm `mode`, `top_k`, `beam_width`,
   `cache_stable`, and the catalog path with `mcp serve --dry-run`.
2. **Route result.** Use `RouteResult.explanation()` for ranked candidates,
   score gaps, filters, and ambiguity. Use `debug=True` when you need the
   expansion trace.
3. **Build statistics.** Check `included_count`, `dropped_count`,
   `dropped_reasons`, per-item `dropped_items`, `dedup_removed`, and token
   usage in `BuildStats`. For an ingested session, run
   `contextweaver inspect --session session.json`.
4. **Artifact reference.** Confirm the handle exists before calling
   `tool_view`, then request a bounded `head`, `lines`, `rows`, or `json_keys`
   selector.
5. **Embedded runtime settings.** If you use `ContextManager` directly,
   inspect phase budgets, the firewall threshold, sensitivity policy, and
   scoring/retrieval backend. These are Python runtime settings, not fields in
   the current `mcp serve` YAML.
6. **Telemetry.** Use `mcp serve --diagnostics FILE` for local JSONL counts,
   savings, failures, artifact-view usage, and latency. The built-in stream
   records IDs, sizes, argument key names, and error codes, but not queries,
   argument values, result text, prompt text, or artifact bytes. When the
   `[otel]` extra is enabled, inspect context-build, firewall, and routing spans.

Do not respond to a poor route by immediately increasing every budget or
returning whole artifacts. Better descriptions, a sharper browse query, and a
narrow view usually preserve more of the gateway's benefit.

## Next steps

- [Claude Desktop recipe](recipes/claude_desktop.md)
- [Claude Code recipe](recipes/claude_code.md)
- [Cursor recipe](recipes/cursor.md)
- [GitHub Copilot recipe](recipes/github_copilot.md)
- [MCP Integration](integration_mcp.md)
- [Adopter Benchmark Report](benchmark_report.md)
- [Troubleshooting](troubleshooting.md)
- [Security Model](security_model.md)

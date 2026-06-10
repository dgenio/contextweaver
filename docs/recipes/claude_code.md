# Claude Code + contextweaver gateway

Use contextweaver as one project-scoped MCP server so Claude Code sees three
gateway meta-tools instead of a large set of full upstream schemas.

This recipe's registration syntax and project config were verified on
**Claude Code 2.1.165 on June 10, 2026**. The deterministic gateway surface is
covered by the repository's MCP tests; live model-driven tool selection
remains a manual client check.

## Prerequisites

1. Claude Code installed and signed in.
2. Python 3.10 or newer.
3. `uv` for the zero-install command, or an installed `contextweaver` CLI.
4. A JSON/YAML tool catalog or MCP `tools/list` snapshot.

The worked example uses the committed 11-tool filesystem snapshot and the
gateway config at `examples/recipes/gateway_config.yaml`.

## Validate before registration

From the contextweaver repository root:

```bash
uvx contextweaver mcp serve \
  --config examples/recipes/gateway_config.yaml \
  --dry-run
```

Expected stderr includes:

```text
mode=gateway ... tools=11 top_k=10 beam_width=3 ...
dry-run: catalog validated; not binding stdio.
```

The first `uvx` invocation may take longer while it resolves an isolated
environment. For a persistent installation, use:

```bash
pip install contextweaver
contextweaver mcp serve --config examples/recipes/gateway_config.yaml --dry-run
```

## Option A: commit `.mcp.json`

The shipped
[`examples/recipes/claude_code_mcp.json`](https://github.com/dgenio/contextweaver/blob/main/examples/recipes/claude_code_mcp.json)
contains:

```json
{
  "mcpServers": {
    "contextweaver-gateway": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "contextweaver",
        "mcp",
        "serve",
        "--config",
        "${CLAUDE_PROJECT_DIR:-.}/examples/recipes/gateway_config.yaml"
      ]
    }
  }
}
```

Copy that structure to `.mcp.json` at the project root and adjust the config
path. Claude Code supports `${VAR}` and `${VAR:-default}` expansion in project
MCP configuration. Keep credentials in environment variables rather than the
committed file.

To pin the package:

```json
"args": [
  "contextweaver@0.14.0",
  "mcp",
  "serve",
  "--config",
  "${CLAUDE_PROJECT_DIR:-.}/examples/recipes/gateway_config.yaml"
]
```

Claude Code asks each user to approve a project-scoped server before
connecting.

## Option B: register the JSON with the Claude CLI

`claude mcp add-json` accepts the server object and writes it to the selected
scope:

PowerShell:

```powershell
claude mcp add-json --scope project contextweaver-gateway `
  '{"type":"stdio","command":"uvx","args":["contextweaver","mcp","serve","--config","${CLAUDE_PROJECT_DIR:-.}/examples/recipes/gateway_config.yaml"]}'
```

macOS/Linux:

```bash
claude mcp add-json --scope project contextweaver-gateway \
  '{"type":"stdio","command":"uvx","args":["contextweaver","mcp","serve","--config","${CLAUDE_PROJECT_DIR:-.}/examples/recipes/gateway_config.yaml"]}'
```

Use `--scope local` for a private entry in the current project or
`--scope user` with an absolute config path for all projects.

Claude Code also documents `claude mcp add <name> -- <command> [args...]`.
On the verified 2.1.165 Windows PowerShell build, a nested server flag such as
`--config` was still parsed as a Claude option. `add-json` and a committed
`.mcp.json` both preserved the arguments correctly, so they are the verified
paths in this recipe.

## Confirm the connection

Run:

```bash
claude mcp list
claude mcp get contextweaver-gateway
```

Then open Claude Code and use `/mcp`. A project-scoped entry may initially
show `Pending approval`; approve it in the interactive session. Once
connected, the server should advertise:

- `tool_browse`
- `tool_execute`
- `tool_view`

It should not advertise the 11 raw filesystem tools from the snapshot.

## Give Claude an operating rule

Add this to the project's `CLAUDE.md` or provide it in the task:

```text
Use contextweaver-gateway for large tool catalogs. Call tool_browse first with
a routing-oriented query, execute only the selected tool_id through
tool_execute, and call tool_view with a narrow selector only when the summary
is insufficient. Do not treat routing as authorization; follow normal approval
rules for tool side effects.
```

Do not keep the same upstream MCP servers registered directly under other
names. Otherwise Claude sees both the raw tools and the gateway.

## Guided first session

1. Open `/mcp` and confirm the three meta-tools.
2. Ask: `Use contextweaver-gateway to find the filesystem tool for listing a directory.`
3. Confirm Claude calls `tool_browse` before `tool_execute`.
4. For a large result, confirm the response is a summary plus an artifact
   handle.
5. Ask for one small slice and confirm Claude uses `tool_view` with `head`,
   `lines`, `rows`, or `json_keys`.

The packaged CLI currently uses a static catalog and deterministic stub
upstream, so this checks client wiring, routing, validation, and firewall
shape. For real upstream calls, wire `McpClientUpstream` or
`MultiplexUpstream` as described in
[MCP Integration](../integration_mcp.md#connecting-to-real-upstream-mcp-servers).

## Troubleshooting

### Gateway is not listed

- Run the dry-run command outside Claude Code first.
- Check `claude mcp get contextweaver-gateway`.
- Approve a project-scoped server in `/mcp`.
- Use an absolute config path for user scope.
- Increase Claude Code's MCP startup timeout if the first cold `uvx` resolve
  exceeds the default.

### Tool names are rejected or missing

The client should see only the gateway's underscore-separated meta-tool names.
If raw upstream names appear, the direct upstream server is still registered.
Remove the duplicate registration and restart the session.

### Server fails after the first call

Stdio servers are not automatically reconnected by Claude Code. Fix the
startup or catalog error, then reconnect from `/mcp` or restart the session.

### Where diagnostics go

`contextweaver mcp serve` writes diagnostics to stderr. Stdout is reserved for
the MCP wire protocol; redirecting application logs to stdout can corrupt the
connection.

### `uvx` is slow or unavailable

Install contextweaver persistently and change the entry to:

```json
"command": "contextweaver",
"args": ["mcp", "serve", "--config", "/absolute/path/to/gateway.yaml"]
```

`pipx run contextweaver mcp serve ...` is also supported for an isolated
invocation.

## Security note

Raw outputs can remain in the artifact store and `tool_view` re-exposes
selected content. Review the [MCP Gateway Security Model](../security_model.md)
before connecting sensitive upstreams.

## See also

- [Daily Driver Guide](../daily_driver.md)
- [Recipes overview](index.md)
- [MCP Integration](../integration_mcp.md)
- [Claude Code MCP documentation](https://code.claude.com/docs/en/mcp)

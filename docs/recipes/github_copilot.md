# GitHub Copilot + contextweaver gateway

Configure VS Code Copilot agent mode to see one contextweaver MCP server with
three meta-tools instead of a large raw tool catalog.

## Prerequisites

1. VS Code with GitHub Copilot and Copilot Chat.
2. Agent mode and MCP support enabled.
3. `uv`, or a persistent `contextweaver` installation.
4. A gateway config and tool catalog.

## Validate the gateway

From the repository root:

```bash
uvx contextweaver mcp serve \
  --config examples/recipes/gateway_config.yaml \
  --dry-run
```

Expected output includes `tools=11` and `dry-run: catalog validated`.

## Workspace-scoped setup

Create `.vscode/mcp.json`:

```json
{
  "$schema": "https://aka.ms/vscode-mcp-schema",
  "servers": {
    "contextweaver-gateway": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "contextweaver",
        "mcp",
        "serve",
        "--config",
        "${workspaceFolder}/examples/recipes/gateway_config.yaml"
      ]
    }
  }
}
```

The exact file ships as
[`examples/recipes/copilot_mcp.json`](https://github.com/dgenio/contextweaver/blob/main/examples/recipes/copilot_mcp.json).
Workspace scope is best when the catalog and config belong to one repository;
`${workspaceFolder}` makes the entry portable across clones.

## User-scoped setup

Run **MCP: Edit Configuration** from the command palette and add the same
server entry. User configuration should use absolute paths because it is not
tied to one workspace:

```json
{
  "servers": {
    "contextweaver-gateway": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "contextweaver",
        "mcp",
        "serve",
        "--config",
        "C:/absolute/path/to/gateway.yaml"
      ]
    }
  }
}
```

Reload the VS Code window. Copilot's Tools picker should list
`contextweaver-gateway` with `tool_browse`, `tool_execute`, and `tool_view`.

## Installed CLI alternative

For a persistent environment:

```json
"command": "contextweaver",
"args": ["mcp", "serve", "--config", "C:/absolute/path/to/gateway.yaml"]
```

Use the executable's absolute path if VS Code does not inherit the shell PATH.

## Agent instruction

Add this to `.github/copilot-instructions.md` or the task prompt:

```text
Use contextweaver-gateway for large tool catalogs. Call tool_browse first with
a routing-oriented query, execute only the selected tool_id through
tool_execute, and use tool_view with a narrow selector only when the summary
is insufficient. Routing does not grant authorization; follow normal approval
rules for side effects.
```

Do not register the same upstream servers directly and behind the gateway.
That exposes both the raw tools and the bounded gateway surface.

## Manual verification

- The Tools picker shows one gateway with three meta-tools.
- A routing request calls `tool_browse` before `tool_execute`.
- A large result returns a summary and artifact handle.
- `tool_view` is used only for a narrow follow-up slice.
- Stopping the server appears as an MCP disconnect rather than a hung turn.

## Live upstream servers

The packaged CLI serves real upstream MCP servers directly: replace the
`catalog:` key in the gateway config with an `upstreams:` block and
`mcp serve` launches every listed server behind the gateway, discovers their
tools, and executes selected calls live — no manual catalog capture required.

```yaml
upstreams:
  filesystem:
    type: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
    namespace: fs
  github:
    type: http
    url: "https://example.com/mcp"
    headers:
      Authorization: "Bearer ${env:GITHUB_TOKEN}"
    namespace: github
startup:
  mode: degraded
```

An existing multi-server `.vscode/mcp.json` migrates in one command:

```bash
contextweaver mcp import-vscode .vscode/mcp.json   # dry-run by default
contextweaver mcp import-vscode .vscode/mcp.json --apply
```

The importer writes an `upstreams:` gateway config from the current server
entries and a replacement client config that lists only the gateway, backing
up the original. See the fault-tolerance knobs under `startup:` in
[MCP Integration](../integration_mcp.md) for degraded vs strict startup.

## 300+ tools

With an `upstreams:` block the gateway discovers the full tool surface at
startup, so a 300-tool setup needs no manual catalog work. A static catalog
(`scripts/capture_mcp_catalog.py` or another `tools/list` importer) remains
useful for offline routing experiments and CI. Either way, do not paste
hundreds of full schemas into Copilot instructions.

## Current limitations

- Live upstream serving covers tools only; resources and prompts over live
  upstreams are not yet bridged (use the static-catalog path for those).
- Prompt-cache behavior depends on the client; `cache_stable` does not force
  Copilot to cache the browse prefix.
- Stdout is reserved for MCP protocol messages. Diagnostics belong on stderr.
- A cold `uvx` resolve can exceed a short MCP startup timeout; install or pin
  the tool when startup latency matters.

## See also

- [Daily Driver Guide](../daily_driver.md)
- [MCP Gateway Security Model](../security_model.md)
- [Recipes overview](index.md)
- [MCP Integration](../integration_mcp.md)

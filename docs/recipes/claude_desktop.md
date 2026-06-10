# Claude Desktop + contextweaver gateway

Configure Claude Desktop to see one contextweaver MCP server with three
gateway meta-tools instead of a large raw tool catalog.

## Prerequisites

1. Claude Desktop on macOS or Windows.
2. Python 3.10 or newer.
3. `uv`, or a persistent `contextweaver` installation.
4. A gateway config and tool catalog.

Claude Desktop's configuration file is:

| OS | Location |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

## Validate the gateway

Before editing Claude's config:

```bash
uvx contextweaver mcp serve \
  --config /absolute/path/to/gateway.yaml \
  --catalog /absolute/path/to/catalog.json \
  --dry-run
```

Passing an explicit absolute `--catalog` is useful for desktop clients whose
server working directory is not predictable. CLI options override values from
the config file.

## Configure Claude Desktop

Add:

```json
{
  "mcpServers": {
    "contextweaver-gateway": {
      "command": "uvx",
      "args": [
        "contextweaver",
        "mcp",
        "serve",
        "--config",
        "/ABSOLUTE/PATH/TO/contextweaver/examples/recipes/gateway_config.yaml",
        "--catalog",
        "/ABSOLUTE/PATH/TO/contextweaver/examples/architectures/mcp_context_gateway/real_catalogs/filesystem.json"
      ],
      "env": {}
    }
  }
}
```

The copy-paste file is
[`examples/recipes/claude_desktop_config.json`](https://github.com/dgenio/contextweaver/blob/main/examples/recipes/claude_desktop_config.json).
Replace both placeholder paths. Claude Desktop does not reliably resolve
shell-relative paths in global configuration.

Restart Claude Desktop. The tool picker should show
`contextweaver-gateway` with:

- `tool_browse`
- `tool_execute`
- `tool_view`

## Installed CLI alternative

After `pip install contextweaver`, use:

```json
"command": "contextweaver",
"args": [
  "mcp",
  "serve",
  "--config",
  "/absolute/path/to/gateway.yaml",
  "--catalog",
  "/absolute/path/to/catalog.json"
]
```

Use the executable's full path if the desktop application does not inherit
your shell PATH.

## Client instruction

Place this in the project/custom instructions:

```text
Use contextweaver-gateway for large tool catalogs. Call tool_browse first,
execute only the selected tool_id through tool_execute, and use tool_view with
a narrow selector only when the summary is insufficient. The gateway does not
authorize side effects; follow normal tool approval rules.
```

## Manual verification

- The tool picker shows the three gateway meta-tools, not all raw tools.
- A request produces a bounded shortlist before execution.
- A large result returns a compact summary and artifact handle.
- A narrow `tool_view` retrieves only the requested slice.
- Stopping the gateway surfaces as a disconnected MCP server.

## Current limitations

- The packaged CLI loads a static catalog and uses a stub upstream handler.
  Live execution needs a Python composition with `McpClientUpstream` or
  `MultiplexUpstream`.
- Claude Desktop may not honor contextweaver's cache-stable marker as a prompt
  caching hint.
- Raw artifacts remain locally accessible and `tool_view` re-exposes selected
  bytes. Review the security model before using sensitive tools.
- Stdout is MCP protocol only; diagnostics are written to stderr.

The legacy
[`serve_gateway.py`](https://github.com/dgenio/contextweaver/blob/main/examples/recipes/serve_gateway.py)
is retained for custom development wiring, not as the default launch path.

## See also

- [Daily Driver Guide](../daily_driver.md)
- [MCP Gateway Security Model](../security_model.md)
- [Claude Code recipe](claude_code.md)
- [MCP Integration](../integration_mcp.md)

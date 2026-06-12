# Cursor + contextweaver gateway

Configure Cursor to expose a bounded contextweaver gateway instead of a large
raw MCP tool set.

## Validate the gateway

```bash
uvx contextweaver mcp serve \
  --config examples/recipes/gateway_config.yaml \
  --dry-run
```

The shipped config loads 11 tools and exposes three gateway meta-tools.

## Project configuration

Add the shipped
[`examples/recipes/cursor_mcp.json`](https://github.com/dgenio/contextweaver/blob/main/examples/recipes/cursor_mcp.json)
as `.cursor/mcp.json`:

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
        "${workspaceFolder}/examples/recipes/gateway_config.yaml"
      ]
    }
  }
}
```

For a global Cursor config, replace `${workspaceFolder}` with an absolute
path. Restart Cursor after changing MCP configuration.

## Installed CLI alternative

```json
"command": "contextweaver",
"args": ["mcp", "serve", "--config", "/absolute/path/to/gateway.yaml"]
```

## What changes

| | Direct upstream | With gateway |
|---|---|---|
| Advertised surface | Every full tool schema | Three meta-tools |
| Per-query choices | Entire catalog | At most `top_k` ChoiceCards |
| Selected schema | Already prompt-visible | Hydrated at execution |
| Large result | Raw content | Summary plus artifact handle |

## Agent instruction

```text
Use contextweaver-gateway for large tool catalogs. Call tool_browse before
tool_execute. Use tool_view only for a narrow slice when the summary does not
answer the question. Keep normal authorization and approval checks.
```

## Current limitations

- The packaged CLI's upstream is a deterministic stub over a static catalog.
  Use `McpClientUpstream` / `MultiplexUpstream` for live upstream execution.
- A cold `uvx` environment can start slowly; install or pin contextweaver for
  daily use if the client startup budget is tight.
- Cursor prompt caching behavior is client-controlled.
- Stdout must contain only MCP wire messages; diagnostics go to stderr.

## See also

- [Daily Driver Guide](../daily_driver.md)
- [MCP Gateway Security Model](../security_model.md)
- [Recipes overview](index.md)
- [MCP Integration](../integration_mcp.md)

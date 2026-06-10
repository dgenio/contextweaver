# MCP Client Recipes

These recipes put the installed `contextweaver mcp serve` command in front of
an MCP client. The default examples use `uvx`, so the client receives an
isolated current release without requiring a persistent Python environment.

| Client | Recipe | Shipped config |
|---|---|---|
| Claude Desktop | [Claude Desktop](claude_desktop.md) | [`claude_desktop_config.json`](https://github.com/dgenio/contextweaver/blob/main/examples/recipes/claude_desktop_config.json) |
| Claude Code | [Claude Code](claude_code.md) | [`claude_code_mcp.json`](https://github.com/dgenio/contextweaver/blob/main/examples/recipes/claude_code_mcp.json) |
| GitHub Copilot in VS Code | [GitHub Copilot](github_copilot.md) | [`copilot_mcp.json`](https://github.com/dgenio/contextweaver/blob/main/examples/recipes/copilot_mcp.json) |
| Cursor | [Cursor](cursor.md) | [`cursor_mcp.json`](https://github.com/dgenio/contextweaver/blob/main/examples/recipes/cursor_mcp.json) |

## Choose an invocation

Zero-install trial:

```bash
uvx contextweaver mcp serve --config /path/to/gateway.yaml --dry-run
```

Persistent installation:

```bash
pip install contextweaver
contextweaver mcp serve --config /path/to/gateway.yaml --dry-run
```

Isolated pipx run:

```bash
pipx run contextweaver mcp serve --config /path/to/gateway.yaml --dry-run
```

The first `uvx` or `pipx run` invocation resolves an environment and may take
longer. Pin the package in managed environments:

```bash
uvx contextweaver@0.14.0 mcp serve --config /path/to/gateway.yaml
pipx run --spec contextweaver==0.14.0 contextweaver mcp serve \
  --config /path/to/gateway.yaml
```

## Shared gateway config

The shipped [`gateway_config.yaml`](https://github.com/dgenio/contextweaver/blob/main/examples/recipes/gateway_config.yaml)
loads the committed 11-tool filesystem snapshot:

```yaml
catalog: ../architectures/mcp_context_gateway/real_catalogs/filesystem.json
mode: gateway
top_k: 10
beam_width: 3
cache_stable: false
name: contextweaver
```

Relative catalog paths are resolved from the gateway config file's directory.
This keeps project-scoped client configs portable even when the client starts
the server from a different working directory.

## What the client sees

```text
MCP client
    |
    +-- tool_browse  -> bounded ChoiceCards
    +-- tool_execute -> hydrated, validated selected call
    +-- tool_view    -> selected artifact slice
```

The client sees three meta-tools instead of every full upstream schema. Large
results become summaries plus artifact handles.

## Current runtime boundary

The packaged CLI loads a static JSON/YAML catalog and uses a deterministic
stub upstream handler. It is suitable for client wiring, tool shortlisting,
argument validation, and firewall-shape checks. Live upstream execution
requires a Python composition using `McpClientUpstream` or
`MultiplexUpstream`; see
[MCP Integration](../integration_mcp.md#connecting-to-real-upstream-mcp-servers).

[`examples/recipes/serve_gateway.py`](https://github.com/dgenio/contextweaver/blob/main/examples/recipes/serve_gateway.py)
remains a legacy/development example for custom `ProxyRuntime` wiring. It is
no longer the default client entry point.

## Large catalogs

For 300+ tools, capture/import the upstream `tools/list`, normalize weak names
and descriptions, and test representative `tool_browse` queries before
deployment. The static snapshot workflow and real catalog fixtures are in the
[MCP Context Gateway architecture](../architectures/mcp_context_gateway.md).

## Next reading

- [Daily Driver Guide](../daily_driver.md)
- [MCP Gateway Security Model](../security_model.md)
- [MCP Integration](../integration_mcp.md)
- [Troubleshooting](../troubleshooting.md)

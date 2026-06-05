# Recipes

> Step-by-step guides for putting contextweaver in front of specific MCP
> clients and real-world MCP servers. Each recipe walks from
> `pip install contextweaver` to a working integration end-to-end.

contextweaver's gateway runtime is a regular MCP server, so any MCP client
can point at it. The recipes here cover the most common clients people ask
about:

| Client | Recipe | Config file |
|---|---|---|
| Claude Desktop | [Claude Desktop](claude_desktop.md) | [`examples/recipes/claude_desktop_config.json`](https://github.com/dgenio/contextweaver/blob/main/examples/recipes/claude_desktop_config.json) |
| GitHub Copilot (VS Code) | [GitHub Copilot](github_copilot.md) | [`examples/recipes/copilot_mcp.json`](https://github.com/dgenio/contextweaver/blob/main/examples/recipes/copilot_mcp.json) |
| Cursor | [Cursor](cursor.md) | [`examples/recipes/cursor_mcp.json`](https://github.com/dgenio/contextweaver/blob/main/examples/recipes/cursor_mcp.json) + [`gateway_config.yaml`](https://github.com/dgenio/contextweaver/blob/main/examples/recipes/gateway_config.yaml) |

> **Zero-Python launch.** `contextweaver mcp serve --config gateway.yaml`
> starts the gateway from a single config file (catalog + `top_k` + mode +
> …) with no Python authoring — explicit CLI flags still win. See the
> [Cursor recipe](cursor.md) for the config-file walkthrough.

Both recipes share the same launcher:
[`examples/recipes/serve_gateway.py`](https://github.com/dgenio/contextweaver/blob/main/examples/recipes/serve_gateway.py).
The launcher takes either `--stub` (built-in 2-tool catalog for sanity
checks) or `--catalog PATH` (a real-MCP-server snapshot from
[`examples/architectures/mcp_context_gateway/real_catalogs/`](https://github.com/dgenio/contextweaver/tree/main/examples/architectures/mcp_context_gateway/real_catalogs)).
When the
[`contextweaver mcp serve`](https://github.com/dgenio/contextweaver/issues/246)
CLI lands, the recipes' launcher line will drop in for the dedicated CLI
with no other changes.

## What "in front of" means

```text
┌────────────────┐    bounded ChoiceCard list    ┌──────────────────┐
│ Claude Desktop │ ─────────────────────────────▶│ contextweaver    │
│ / VS Code      │                               │  gateway (stdio) │
└────────────────┘ ◀────────────────────────────│                  │
                       firewalled tool result   └────────┬─────────┘
                                                         │ raw tools/list
                                                         ▼
                                              ┌───────────────────┐
                                              │ Upstream MCP      │
                                              │  server(s)        │
                                              └───────────────────┘
```

The MCP client sees one virtual MCP server that exposes a bounded list of
`ChoiceCards` instead of the upstream's full tool catalogue, and receives
a firewalled summary (plus an artifact handle) instead of the raw tool
output. The upstream servers are unchanged — you do not have to fork or
patch them.

## See also

- [MCP Integration](../integration_mcp.md) — the underlying adapter
  surface (`mcp_tool_to_selectable`, `mcp_result_to_envelope`,
  `ProxyRuntime`, `McpGatewayServer`).
- [`docs/gateway_spec.md`](../gateway_spec.md) — the normative
  meta-tool grammar (`tool_browse`, `tool_execute`, `tool_view`).
- [Architectures > MCP Context Gateway](../architectures/mcp_context_gateway.md)
  — the worked reference architecture both recipes are derived from.

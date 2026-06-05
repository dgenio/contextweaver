# Cursor + contextweaver gateway

> Put contextweaver in front of one or more MCP servers so
> [Cursor](https://cursor.com) sees a bounded `ChoiceCard` shortlist instead
> of every tool from every upstream server, and receives firewalled summaries
> instead of raw tool output.

This recipe uses the **zero-Python, config-file launch** added for
[issue #346](https://github.com/dgenio/contextweaver/issues/346): one config
file plus one command, no Python authoring. Manual validation against a
running Cursor install is still required — see *What to verify manually*.

## What you will end up with

```text
┌─────────────────┐    tool_browse / tool_execute     ┌──────────────────────┐
│ Cursor          │ ────────────────────────────────▶ │ contextweaver        │
│ (stdio client)  │                                   │  gateway             │
└─────────────────┘ ◀──────────────────────────────── │                      │
                       bounded ChoiceCards +          └────────────┬─────────┘
                       firewalled tool results                     │ raw MCP
                                                                   ▼
                                                       ┌────────────────────┐
                                                       │ Real MCP server    │
                                                       └────────────────────┘
```

## Prerequisites

1. **Python ≥ 3.10** and `pip`.
2. **Cursor**, which reads MCP servers from `~/.cursor/mcp.json` (global) or
   `.cursor/mcp.json` (per-project).
3. A tool catalog. This recipe fronts the committed filesystem snapshot at
   [`examples/architectures/mcp_context_gateway/real_catalogs/filesystem.json`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/mcp_context_gateway/real_catalogs/filesystem.json)
   so it reproduces offline.

## Step 1 — Install contextweaver

```bash
pip install contextweaver
```

This puts the `contextweaver` CLI on your `PATH`.

## Step 2 — Write one config file

contextweaver reads a single config file describing the catalog and the
gateway defaults. Start from the shipped example
[`examples/recipes/gateway_config.yaml`](https://github.com/dgenio/contextweaver/blob/main/examples/recipes/gateway_config.yaml):

```yaml
catalog: examples/architectures/mcp_context_gateway/real_catalogs/filesystem.json
mode: gateway
top_k: 10
beam_width: 3
cache_stable: false
name: contextweaver
```

Every key except `catalog` is optional and defaults to the CLI default.
Validate it without binding stdio:

```bash
contextweaver mcp serve --config examples/recipes/gateway_config.yaml --dry-run
# contextweaver mcp serve: mode=gateway catalog=... tools=11 top_k=10 ...
# dry-run: catalog validated; not binding stdio.
```

## Step 3 — Point Cursor at the gateway

Add the gateway to `~/.cursor/mcp.json` (or copy the shipped
[`examples/recipes/cursor_mcp.json`](https://github.com/dgenio/contextweaver/blob/main/examples/recipes/cursor_mcp.json)):

```json
{
  "mcpServers": {
    "contextweaver-gateway": {
      "command": "contextweaver",
      "args": ["mcp", "serve", "--config", "/abs/path/to/gateway_config.yaml"]
    }
  }
}
```

Use an absolute path to the config file — Cursor does not expand
`${workspaceFolder}` for global configs. Restart Cursor; the gateway appears
as one MCP server exposing the `tool_browse` / `tool_execute` / `tool_view`
meta-tools.

## Before / after

Fronting the committed `filesystem.json` snapshot (11 upstream tools) with
`mode: gateway` and `top_k: 10`:

| | Without gateway | With gateway |
|---|---|---|
| Tools advertised to Cursor | 11 full schemas | 3 meta-tools (`tool_browse`/`tool_execute`/`tool_view`) |
| Per-query tool surface | all 11 schemas in the prompt | ≤ `top_k` bounded `ChoiceCard`s, no `args_schema` until selected |
| Large tool result | raw bytes in the prompt | firewalled summary + artifact handle |

The reduction grows with catalog size — the packaged 60-tool gateway catalog
(`python -c "from contextweaver.data import gateway_catalog_path; print(gateway_catalog_path())"`)
collapses 60 schemas to the same 3 meta-tools.

## What to verify manually

- Cursor lists `contextweaver-gateway` and its three meta-tools after restart.
- `tool_browse` returns a bounded shortlist for a natural-language query.
- A large `tool_execute` result comes back as a summary + handle, not raw bytes.

## Current limitation

The bundled `mcp serve` upstream is a **stub** handler (it echoes calls) so the
end-to-end wiring and shortlisting are exercisable offline. Bridging to a
*live* upstream MCP server over stdio is tracked as follow-up on
[issue #346](https://github.com/dgenio/contextweaver/issues/346); until then,
use the catalog snapshot for shortlisting and the
[`serve_gateway.py`](https://github.com/dgenio/contextweaver/blob/main/examples/recipes/serve_gateway.py)
launcher's `build_runtime_from_snapshot` for programmatic upstream wiring.

## See also

- [Recipes overview](index.md)
- [Claude Desktop recipe](claude_desktop.md)
- [GitHub Copilot recipe](github_copilot.md)
- [MCP Context Gateway architecture](../architectures/mcp_context_gateway.md)

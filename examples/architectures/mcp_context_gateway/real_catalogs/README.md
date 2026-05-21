# Real-MCP catalog snapshots

This directory ships verbatim `tools/list` payloads captured from official,
MIT-licensed [Model Context Protocol reference servers](https://github.com/modelcontextprotocol/servers).
They feed `main_real.py`, which runs the same DevOps-Copilot flow as
[`main.py`](../main.py) but against real tool names, descriptions, and
JSON Schemas — answering the natural follow-up to the mocked 60-tool
demo: *"How does this look on a real MCP server?"*

## Snapshots

| File | Upstream server | Tools | License |
|---|---|---|---|
| [`filesystem_mcp.json`](filesystem_mcp.json) | `@modelcontextprotocol/server-filesystem` | 11 | MIT |
| [`git_mcp.json`](git_mcp.json) | `mcp-server-git` | 12 | MIT |
| [`fetch_mcp.json`](fetch_mcp.json) | `mcp-server-fetch` | 1 | MIT |

Each file is a JSON object with two top-level keys:

- `_meta` — provenance: upstream source, server version, license, snapshot
  date, snapshot method, and notes.
- `tools` — a list of raw `tools/list` entries (`name`, `description`,
  `inputSchema`, optional `annotations`) consumable by
  [`ProxyRuntime.register_tool_defs_sync`](../../../src/contextweaver/adapters/proxy_runtime.py)
  or [`mcp_tool_to_selectable`](../../../src/contextweaver/adapters/mcp.py).

## Re-snapshotting

Snapshots drift as upstream servers ship new releases. Re-snapshotting is
one command — point the helper at any MCP server reachable over stdio:

```bash
python scripts/snapshot_mcp_catalog.py \
    --command "npx -y @modelcontextprotocol/server-filesystem /tmp" \
    --source-name "@modelcontextprotocol/server-filesystem" \
    --server-version 2025.10.1 \
    --license MIT \
    --output examples/architectures/mcp_context_gateway/real_catalogs/filesystem_mcp.json
```

The helper writes back the same `{_meta, tools}` shape this directory
expects, so the re-snapshot is a drop-in replacement that keeps
`main_real.py` working.

## Licence & attribution

All three snapshots are derivative works of MIT-licensed servers from
`github.com/modelcontextprotocol/servers`. The MIT licence permits
redistribution with attribution; the canonical attribution lives inside
each file's `_meta.source` / `_meta.license` fields and is repeated in the
table above. The full upstream licence text is at
[`modelcontextprotocol/servers/LICENSE`](https://github.com/modelcontextprotocol/servers/blob/main/LICENSE).

When adding a new snapshot to this directory:

1. Confirm the upstream server is MIT, Apache-2.0, or another
   redistribution-friendly licence.
2. Populate `_meta.license` and `_meta.license_url` with the upstream's
   SPDX identifier and a stable URL to the licence text.
3. Add a row to the table above.

## See also

- [`../main.py`](../main.py) — the mocked 60-tool reference architecture.
- [`../main_real.py`](../main_real.py) — runs the gateway flow against
  these real snapshots.
- [`../../../docs/recipes/claude_desktop.md`](../../../docs/recipes/claude_desktop.md)
  and [`../../../docs/recipes/github_copilot.md`](../../../docs/recipes/github_copilot.md)
  — recipes for putting `contextweaver` in front of real MCP servers
  from real MCP clients.

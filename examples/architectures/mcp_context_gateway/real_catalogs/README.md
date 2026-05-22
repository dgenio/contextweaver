# Real-MCP catalog snapshots

> Committed snapshots of the `tools/list` response from real public MCP
> servers. Loaded by `main_real.py` so the architecture can be
> demonstrated against catalogs that were not hand-tuned for the demo.

Each `*.json` file is a flat list of MCP tool definitions in the wire
shape (`name`, `description`, `inputSchema`). Snapshots are committed so
the demo stays network-free and deterministic; regenerate them with
`scripts/capture_mcp_catalog.py` when an upstream server adds or renames
tools.

| File | Source | Tools | Notes |
|---|---|---:|---|
| `time.json` | [`@modelcontextprotocol/server-time`](https://github.com/modelcontextprotocol/servers/tree/main/src/time) | 2 | Smallest official MCP server — get current time, convert between timezones. |
| `filesystem.json` | [`@modelcontextprotocol/server-filesystem`](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem) | 11 | Read/write/list/move files; widely deployed by Claude Desktop users. |
| `everything.json` | [`@modelcontextprotocol/server-everything`](https://github.com/modelcontextprotocol/servers/tree/main/src/everything) | 9 | Official "kitchen-sink" reference server — every MCP primitive. |

## Regenerating a snapshot

```bash
# Stand up the upstream server in another terminal (one of):
npx -y @modelcontextprotocol/server-time
npx -y @modelcontextprotocol/server-filesystem /tmp
npx -y @modelcontextprotocol/server-everything

# Capture its tools/list (stdio transport assumed):
python scripts/capture_mcp_catalog.py \
    --from-command "npx -y @modelcontextprotocol/server-time" \
    --output examples/architectures/mcp_context_gateway/real_catalogs/time.json
```

The capture script is offline-safe — if the upstream server is unreachable
it leaves the existing snapshot untouched.

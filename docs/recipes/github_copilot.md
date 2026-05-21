# GitHub Copilot + contextweaver gateway

> Put contextweaver in front of one or more MCP servers so VS Code's
> [GitHub Copilot Chat agent mode](https://docs.github.com/en/copilot)
> sees a bounded `ChoiceCard` shortlist instead of every tool from every
> upstream server, and receives firewalled summaries instead of raw tool
> output.

This recipe addresses [issue #279](https://github.com/dgenio/contextweaver/issues/279)
and is the sibling of the [Claude Desktop recipe](claude_desktop.md). The
flow is identical; the only difference is the config-file shape that
Copilot consumes.

## What you will end up with

```text
┌─────────────────────────┐   tool_browse / tool_execute   ┌───────────────────────┐
│ VS Code + Copilot Chat  │ ─────────────────────────────▶│ contextweaver gateway │
│  (agent mode, stdio)    │                               │  (this recipe)        │
└─────────────────────────┘ ◀──────────────────────────── │                       │
                              bounded ChoiceCards +       └───────────┬───────────┘
                              firewalled tool results                 │
                                                                      ▼
                                                          ┌────────────────────┐
                                                          │ Real MCP server(s) │
                                                          └────────────────────┘
```

Copilot Chat talks to exactly one virtual MCP server called
`contextweaver-gateway`. Under the hood that server proxies to one or
more real upstream MCP servers.

## Prerequisites

1. **Python ≥ 3.10** and a working `pip`.
2. **VS Code** with the
   [GitHub Copilot](https://marketplace.visualstudio.com/items?itemName=GitHub.copilot)
   and
   [GitHub Copilot Chat](https://marketplace.visualstudio.com/items?itemName=GitHub.copilot-chat)
   extensions, signed in, and **agent mode** enabled — see the
   [Copilot agent-mode docs](https://docs.github.com/en/copilot/customizing-copilot/extending-copilot-chat-with-mcp).
3. A real MCP server you want to front. As in the Claude Desktop recipe,
   we use
   [`@modelcontextprotocol/server-filesystem`](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem)
   for the worked example because the snapshot is committed and
   reproducible.

## Step 1 — Install contextweaver

```bash
pip install contextweaver
```

## Step 2 — Smoke-test the launcher

Before wiring Copilot, validate the gateway launcher boots:

```bash
python /path/to/contextweaver/examples/recipes/serve_gateway.py --stub
```

Expect `contextweaver gateway ready (2 upstream tools, mode=GATEWAY)` on
stderr. `Ctrl-C` to stop.

## Step 3 — Point the launcher at a real upstream snapshot

```bash
python /path/to/contextweaver/examples/recipes/serve_gateway.py \
    --catalog /path/to/contextweaver/examples/architectures/mcp_context_gateway/real_catalogs/filesystem_mcp.json
```

The committed snapshot is verbatim from the MIT-licensed
`@modelcontextprotocol/server-filesystem` reference server.
Re-snapshot with
[`scripts/snapshot_mcp_catalog.py`](https://github.com/dgenio/contextweaver/blob/main/scripts/snapshot_mcp_catalog.py)
when the upstream ships a new version.

## Step 4 — Wire Copilot via `.vscode/mcp.json`

Copilot Chat in agent mode reads MCP server definitions from one of:

- **Workspace-scoped:** `.vscode/mcp.json` at the repo root (recommended
  for project-specific tool catalogues).
- **User-scoped:** the *MCP: Edit Configuration* command in the command
  palette, which opens your user-level config file.

For workspace scope, create `.vscode/mcp.json` with:

```json
{
  "$schema": "https://aka.ms/vscode-mcp-schema",
  "servers": {
    "contextweaver-gateway": {
      "type": "stdio",
      "command": "python",
      "args": [
        "${workspaceFolder}/examples/recipes/serve_gateway.py",
        "--catalog",
        "${workspaceFolder}/examples/architectures/mcp_context_gateway/real_catalogs/filesystem_mcp.json"
      ]
    }
  }
}
```

A copy-pasteable version of this exact file lives at
[`examples/recipes/copilot_mcp.json`](https://github.com/dgenio/contextweaver/blob/main/examples/recipes/copilot_mcp.json).
`${workspaceFolder}` is expanded by VS Code at load time, so the same
file works on every developer's machine that clones the repo.

If you prefer the user-scoped MCP config, replace `${workspaceFolder}`
with absolute paths — VS Code's user-config loader does not expand
workspace variables.

Reload VS Code (*Developer: Reload Window* in the command palette).
Copilot Chat's *Tools* picker should now list `contextweaver-gateway`
exposing the three meta-tools (`tool_browse`, `tool_execute`,
`tool_view`).

## Step 5 — Multi-upstream fan-out

Same pattern as the Claude Desktop recipe — one contextweaver gateway in
front of N real MCP servers using
[`MultiplexUpstream`](https://github.com/dgenio/contextweaver/blob/main/src/contextweaver/adapters/mcp_upstream.py):

```python
from contextweaver.adapters import ExposureMode, ProxyRuntime
from contextweaver.adapters.mcp_upstream import MultiplexUpstream

runtime = ProxyRuntime(MultiplexUpstream([fs_upstream, git_upstream, fetch_upstream]),
                       mode=ExposureMode.GATEWAY)
```

Swap `examples/recipes/serve_gateway.py` for a script that builds the
runtime the way you need it; Copilot's `.vscode/mcp.json` does not
change.

## Step 6 — Use it from Copilot Chat

Open Copilot Chat, switch to *Agent* mode, and ask something the upstream
can answer:

> *"What Python files in this workspace import `httpx`?"*

Copilot should pick `tool_browse` itself, see a bounded ChoiceCard
shortlist for the filesystem upstream, then call `tool_execute` against
the chosen tool. The raw `search_files` output gets firewalled — Copilot
sees a summary and a `tool_view` handle it can drill into.

## What to verify manually

VS Code + Copilot agent mode is not reachable from contextweaver's CI.
After wiring, validate manually:

- [ ] Copilot's *Tools* picker shows exactly `contextweaver-gateway`
      after the reload, listing the three meta-tools — not the
      11 raw filesystem tools.
- [ ] A simple query returns a bounded ChoiceCard shortlist, then a
      firewalled summary.
- [ ] Stopping `serve_gateway.py` cleanly surfaces in Copilot Chat as a
      tool disconnect rather than a hung agent turn.
- [ ] `tool_view` against the returned artifact handle drills into the
      original output without leaking the raw bytes back into chat
      history.

## What does *not* work yet

- **`cache_stable` prompt caching.** `ProxyRuntime(cache_stable=True)`
  produces a byte-stable cache-breakpoint marker on `tool_browse`. VS
  Code's Copilot client does not yet honour the marker. Behaviour is
  correct; the latency win is forward-looking.
- **Per-tool latency budgets.** Copilot agent mode does not yet expose a
  per-MCP-server latency budget. `serve_gateway.py` runs at the speed of
  the slowest upstream — wire `tool_execute` calls to your upstream with
  its own timeout if you need one.
- **Dedicated `contextweaver mcp serve` CLI.** Tracked in
  [#246](https://github.com/dgenio/contextweaver/issues/246) (currently
  in flight). Once that lands, replace the launcher line in
  `.vscode/mcp.json` with `"command": "contextweaver", "args": ["mcp",
  "serve", "--catalog", "..."]`. The rest is unchanged.

## See also

- [Claude Desktop recipe](claude_desktop.md) — sibling recipe with the
  identical contextweaver-side wiring.
- [`docs/integration_mcp.md`](../integration_mcp.md) — full adapter
  surface (`mcp_tool_to_selectable`, `mcp_result_to_envelope`,
  `ProxyRuntime`, `MultiplexUpstream`, `McpGatewayServer`).
- [Real-MCP catalog snapshots](https://github.com/dgenio/contextweaver/tree/main/examples/architectures/mcp_context_gateway/real_catalogs)
  — committed `tools/list` snapshots ready to point the launcher at.
- [`scripts/snapshot_mcp_catalog.py`](https://github.com/dgenio/contextweaver/blob/main/scripts/snapshot_mcp_catalog.py)
  — re-snapshot a server when upstream ships a new version.

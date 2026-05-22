# Claude Desktop + contextweaver gateway

> Put contextweaver in front of one or more MCP servers so
> [Claude Desktop](https://claude.ai/download) sees a bounded
> `ChoiceCard` shortlist instead of every tool from every upstream server,
> and receives firewalled summaries instead of raw tool output.

This recipe addresses [issue #278](https://github.com/dgenio/contextweaver/issues/278).
Manual validation against a running Claude Desktop install is still
required — see the *What to verify manually* section.

## What you will end up with

```text
┌─────────────────┐    tool_browse / tool_execute     ┌──────────────────────┐
│ Claude Desktop  │ ────────────────────────────────▶ │ contextweaver        │
│ (stdio client)  │                                   │  gateway (this PR)   │
└─────────────────┘ ◀──────────────────────────────── │                      │
                       bounded ChoiceCards +          └────────────┬─────────┘
                       firewalled tool results                     │ raw MCP
                                                                   ▼
                                                       ┌────────────────────┐
                                                       │ Real MCP server    │
                                                       │  (filesystem/git/  │
                                                       │   fetch/your own)  │
                                                       └────────────────────┘
```

Claude Desktop talks to exactly one "MCP server" called
`contextweaver-gateway`. Under the hood that server is a Python process
running the gateway and proxying to one or more real upstream MCP
servers.

## Prerequisites

1. **Python ≥ 3.10** and a working `pip` (matches contextweaver's core
   requirement).
2. **Claude Desktop**, freshly installed — the
   [download page](https://claude.ai/download) ships builds for macOS and
   Windows. The `claude_desktop_config.json` location depends on your OS:

    | OS | Config file location |
    |---|---|
    | macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
    | Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

3. A real MCP server you want to front. Any server is fine; this recipe
   uses the official MIT-licensed
   [`@modelcontextprotocol/server-filesystem`](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem)
   as the worked example, since its tool surface is committed under
   [`examples/architectures/mcp_context_gateway/real_catalogs/filesystem.json`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/mcp_context_gateway/real_catalogs/filesystem.json)
   for offline reproduction.

## Step 1 — Install contextweaver locally

```bash
pip install contextweaver
```

If you cloned the repository for development, an editable install also
works (`pip install -e .`). No additional optional extras are required
for the gateway path — `mcp` is already a core dependency.

## Step 2 — Run the gateway against a stub catalog

Sanity-check that the gateway launcher boots cleanly **before** wiring
Claude Desktop:

```bash
python /path/to/contextweaver/examples/recipes/serve_gateway.py --stub
```

The launcher logs `contextweaver gateway ready (2 upstream tools, …)` to
stderr and then blocks on stdio. Hit `Ctrl-C` to stop it. If you see an
import error here, fix it first — Claude Desktop will surface the same
error as a cryptic *Server failed to start* notification.

## Step 3 — Switch to a real upstream

The stub catalog is for plumbing checks. To actually do work, point the
launcher at a real `tools/list` snapshot from a real MCP server:

```bash
python /path/to/contextweaver/examples/recipes/serve_gateway.py \
    --catalog /path/to/contextweaver/examples/architectures/mcp_context_gateway/real_catalogs/filesystem.json
```

The launcher should now report `11 upstream tools, mode=GATEWAY` on
stderr. The committed snapshot is verbatim from the official
`@modelcontextprotocol/server-filesystem` package; re-snapshot from a
live server with
[`scripts/capture_mcp_catalog.py`](https://github.com/dgenio/contextweaver/blob/main/scripts/capture_mcp_catalog.py)
when upstream ships a new version.

> **Working against the *live* MCP server:** the recipe above uses a
> committed snapshot so it is reproducible offline. For live traffic,
> swap the launcher to wire `McpClientUpstream(session)` against your
> actual upstream — see
> [MCP Integration > Connecting to real upstream MCP servers](../integration_mcp.md#connecting-to-real-upstream-mcp-servers).
> The Claude Desktop side of the wiring is identical in both cases.

## Step 4 — Wire Claude Desktop

Open Claude Desktop's `claude_desktop_config.json` (location table above)
and paste:

```json
{
  "mcpServers": {
    "contextweaver-gateway": {
      "command": "python",
      "args": [
        "/ABSOLUTE/PATH/TO/contextweaver/examples/recipes/serve_gateway.py",
        "--catalog",
        "/ABSOLUTE/PATH/TO/contextweaver/examples/architectures/mcp_context_gateway/real_catalogs/filesystem.json"
      ],
      "env": {}
    }
  }
}
```

A copy-pasteable version of this exact file lives at
[`examples/recipes/claude_desktop_config.json`](https://github.com/dgenio/contextweaver/blob/main/examples/recipes/claude_desktop_config.json).
**Replace `/ABSOLUTE/PATH/TO/contextweaver/` with the directory where you
cloned (or `pip install`-extracted) the repository.** Claude Desktop's
loader does not expand `~` or relative paths.

Restart Claude Desktop. The tool icon in the prompt bar should now list
`contextweaver-gateway` (or your `name` value) with three meta-tools:
`tool_browse`, `tool_execute`, `tool_view` (from
[gateway_spec.md §4.2](../gateway_spec.md)).

## Step 5 — Fan-out: one gateway in front of multiple upstreams

The most useful pattern is *one* contextweaver gateway in front of *N*
real MCP servers — that is what bounds the ChoiceCard list to a top-k
shortlist no matter how many tools the upstreams expose collectively.
The supported wiring is:

```python
# Substitute for examples/recipes/serve_gateway.py when you need
# multi-upstream fan-out — see docs/integration_mcp.md.
from contextweaver.adapters import ExposureMode, ProxyRuntime
from contextweaver.adapters.mcp_upstream import MultiplexUpstream

runtime = ProxyRuntime(MultiplexUpstream([fs_upstream, git_upstream, fetch_upstream]),
                       mode=ExposureMode.GATEWAY)
```

`MultiplexUpstream` is already part of contextweaver
([`adapters/mcp_upstream.py`](https://github.com/dgenio/contextweaver/blob/main/src/contextweaver/adapters/mcp_upstream.py)).
The committed snapshots under
[`real_catalogs/`](https://github.com/dgenio/contextweaver/tree/main/examples/architectures/mcp_context_gateway/real_catalogs)
are exactly the input shape it expects per upstream.

## Step 6 — Use it from Claude Desktop

Ask Claude something the upstream can answer, for example:

> *"List the JavaScript files under `/tmp/my-project`."*

Behind the scenes Claude Desktop will call `tool_browse` (Claude picks
the verb), get a bounded shortlist from contextweaver, then call
`tool_execute` against the chosen tool. The raw `list_directory` output
is firewalled — Claude sees a summary plus a `tool_view` handle it can
drill into on demand.

The
[MCP Context Gateway architecture](../architectures/mcp_context_gateway.md)
walks the same flow end-to-end with deterministic, captured output if
you want to read what's happening at each step before validating live.

## What to verify manually

Live validation cannot run inside contextweaver's CI (no headless Claude
Desktop). Walk this checklist after wiring:

- [ ] Claude Desktop's *Tools* picker shows `contextweaver-gateway` after
      restart, exposing the three meta-tools — not the raw 11 filesystem
      tools.
- [ ] A simple query (e.g. *"list files in /tmp"*) returns a
      bounded ChoiceCard shortlist, then a firewalled summary.
- [ ] `tool_view` against the returned artifact handle drills into the
      original output without leaking sensitive paths back into the
      Claude conversation history.
- [ ] Stopping the launcher (`Ctrl-C` in the terminal that runs
      `serve_gateway.py`) cleanly surfaces in Claude Desktop as a "Server
      disconnected" notice rather than a process hang.

## What does *not* work yet

- **`cache_stable` prompt caching.** contextweaver's
  [`ProxyRuntime(cache_stable=True)`](https://github.com/dgenio/contextweaver/blob/main/src/contextweaver/adapters/proxy_runtime.py)
  produces a byte-stable cache-breakpoint marker on `tool_browse`, but
  Claude Desktop does not currently honour the marker as a cache hint.
  Behaviour is correct; the latency win is forward-looking.
- **Live tool execution against an upstream that requires environment
  secrets.** The recipe uses a snapshot, so the *catalogue* path is
  deterministic. Real `tool_execute` traffic needs you to set the
  upstream server's required env vars (e.g. `GITHUB_PERSONAL_ACCESS_TOKEN`
  for `github-mcp`) — these flow through `serve_gateway.py`'s subprocess
  environment when you swap `StubUpstream` for a real `McpClientUpstream`.
- **Dedicated `contextweaver mcp serve` CLI.** Tracked in
  [#246](https://github.com/dgenio/contextweaver/issues/246) (currently in
  flight). Once that lands, replace the `command` / `args` block above
  with `"command": "contextweaver", "args": ["mcp", "serve", "--catalog",
  "..."]`. The rest of the recipe is unchanged.

## See also

- [GitHub Copilot recipe](github_copilot.md) — sibling recipe for the
  VS Code Copilot Chat MCP integration.
- [`docs/integration_mcp.md`](../integration_mcp.md) — full adapter
  surface (`mcp_tool_to_selectable`, `mcp_result_to_envelope`,
  `ProxyRuntime`, `MultiplexUpstream`, `McpGatewayServer`).
- [Real-MCP catalog snapshots](https://github.com/dgenio/contextweaver/tree/main/examples/architectures/mcp_context_gateway/real_catalogs)
  — committed `tools/list` snapshots ready to point the launcher at.
- [`scripts/capture_mcp_catalog.py`](https://github.com/dgenio/contextweaver/blob/main/scripts/capture_mcp_catalog.py)
  — re-snapshot a server when upstream ships a new version.

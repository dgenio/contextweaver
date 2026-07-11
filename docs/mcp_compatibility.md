# MCP Specification Compatibility

contextweaver's gateway and proxy speak MCP through the official
[`mcp` Python SDK](https://github.com/modelcontextprotocol/python-sdk)
(core dependency, floor `mcp>=1.19.0`). Protocol-version negotiation is
therefore delegated to the SDK: contextweaver adds no wire-format code of its
own, and inherits new spec revisions by raising the SDK floor. (Issue #548.)

## Version matrix

| MCP spec revision | SDK support (at floor `1.19`) | contextweaver status |
|---|---|---|
| `2024-11-05` | Negotiated | Supported via SDK negotiation |
| `2025-03-26` | Negotiated (SDK default fallback) | Supported; default negotiated version for clients that do not send one |
| `2025-06-18` | Negotiated | Supported; structured tool output and elicitation are SDK-side — the gateway's meta-tools return plain text content and are unaffected |
| `2025-11-25` (latest at time of writing) | `LATEST_PROTOCOL_VERSION` | Supported via SDK negotiation |

The authoritative list is `mcp.shared.version.SUPPORTED_PROTOCOL_VERSIONS` in
the installed SDK — check it with:

```bash
python -c "from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS as v; print(v)"
```

## Transports

| Transport | Gateway/proxy support | Notes |
|---|---|---|
| stdio | Yes (default) | `contextweaver mcp serve` |
| SSE | Yes | `--transport sse`; DNS-rebinding protection on by default (issue #694) |
| Streamable HTTP | Yes | `--transport streamable-http`; session-id + protocol-version header handling via the SDK (issues #422/#665) |

## MCP surface coverage

| MCP feature | Coverage |
|---|---|
| Tools (`tools/list`, `tools/call`) | Full — the gateway's core surface |
| Resources / prompts | Static-catalog gateway meta-tools (`resource_browse` / `resource_read` / `prompt_browse` / `prompt_get`, issues #669/#670); not yet bridged over live upstreams |
| `notifications/tools/list_changed` | Consumed from live upstreams for incremental catalog refresh (issue #424, opt-in) |
| Sampling | Opt-in `call_fn` bridge for firewall summaries (issue #623); the gateway never initiates sampling on its own |
| Elicitation | Not used |

## 2026 stateless-core / Tasks readiness

The draft 2026 revision direction (stateless core, long-running Tasks) is
tracked upstream. contextweaver's exposure assessment:

- **Stateless core** — low risk. The gateway already treats each meta-tool
  call independently; per-session state (artifact store, event log) is keyed
  by session id and survives via `--state-dir`. If session establishment
  becomes optional, the gateway can key state by a client-supplied
  correlation id instead. No wire code changes on our side; wait for the SDK.
- **Tasks (long-running operations)** — moderate opportunity. `tool_execute`
  is currently synchronous; a Tasks-shaped SDK API would map naturally onto
  the artifact store (result lands as an artifact; `tool_view` polls/slices).
  Design work tracked separately once the revision and SDK support are final.

**Policy:** contextweaver raises the SDK floor deliberately (each raise is
verified by the gating floor-deps CI job — see the dependency-constraint
policy in `AGENTS.md`) and does not pre-implement draft revisions.

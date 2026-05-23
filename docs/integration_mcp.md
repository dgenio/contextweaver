# MCP Integration

contextweaver provides an adapter for the
[Model Context Protocol (MCP)](https://modelcontextprotocol.io/) that
converts MCP tool definitions and results into contextweaver's native
types.

## Adapter functions

### `mcp_tool_to_selectable(tool_dict)`

Converts an MCP tool definition dict into a `SelectableItem`:

```python
from contextweaver.adapters.mcp import mcp_tool_to_selectable

mcp_tool = {
    "name": "search_database",
    "description": "Search records in the database",
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 10}
        }
    },
    "outputSchema": {
        "type": "object",
        "properties": {
            "results": {"type": "array"},
            "total": {"type": "integer"}
        }
    }
}

item = mcp_tool_to_selectable(mcp_tool)
# item.id            == "mcp:search_database"
# item.kind          == "tool"
# item.name          == "search_database"
# item.output_schema == {"type": "object", ...}
```

If the tool definition includes an `outputSchema`, it is preserved in
`item.output_schema`.  When absent the field is `None`.

The namespace is inferred automatically from the tool name prefix:

| Tool name              | Inferred namespace |
|------------------------|--------------------|
| `github.create_issue`  | `github`           |
| `filesystem/read`      | `filesystem`       |
| `slack_send_message`   | `slack`            |
| `search_database`      | `mcp` (fallback)   |

Use `infer_namespace(tool_name)` directly if you need the logic outside of
`mcp_tool_to_selectable()`.

### `mcp_result_to_envelope(result_dict, tool_name)`

Converts an MCP tool result dict into a `ResultEnvelope`:

```python
from contextweaver.adapters.mcp import mcp_result_to_envelope

mcp_result = {
    "content": [{"type": "text", "text": "Found 42 records matching query"}],
    "isError": False
}

envelope, binaries, full_text = mcp_result_to_envelope(mcp_result, "search_database")
# envelope.summary contains truncated text (max 500 chars)
# full_text contains the complete untruncated text
# envelope.status  == "ok"
# binaries maps handle → (raw_bytes, media_type, label)
```

#### Supported content types

| Content type    | Handling                                                                                      |
|-----------------|-----------------------------------------------------------------------------------------------|
| `text`          | Concatenated into `full_text` and `summary`                                                   |
| `image`         | Base64-decoded; stored as binary artifact                                                     |
| `audio`         | Base64-decoded; stored as binary artifact (e.g. `audio/wav`)                                  |
| `resource`      | Text extracted into `full_text`; raw bytes stored as artifact                                 |
| `resource_link` | URI stored as `ArtifactRef`; URI string in `binaries` for caller resolution                |

#### Structured content

If the result contains a top-level `structuredContent` dict, it is
serialized as a JSON artifact and its top-level keys are extracted as
facts:

```python
mcp_result = {
    "content": [{"type": "text", "text": "query done"}],
    "structuredContent": {"count": 42, "status": "done"},
}
envelope, binaries, _ = mcp_result_to_envelope(mcp_result, "query")
# binaries["mcp:query:structured_content"] → JSON bytes
# envelope.facts includes "count: 42", "status: done"
```

#### Content-part annotations

Per-part `annotations` (with `audience` and `priority` fields) are
collected into `envelope.provenance["content_annotations"]`:

```python
mcp_result = {
    "content": [
        {"type": "text", "text": "...", "annotations": {"audience": ["human"], "priority": 0.9}},
    ],
}
envelope, _, _ = mcp_result_to_envelope(mcp_result, "tool")
# envelope.provenance["content_annotations"] == [{"part_index": 0, "audience": ["human"], ...}]
```

### `load_mcp_session_jsonl(path)`

Loads a JSONL session file containing MCP-style events and returns a
list of `ContextItem` objects:

```python
from contextweaver.adapters.mcp import load_mcp_session_jsonl

items = load_mcp_session_jsonl("examples/data/mcp_session.jsonl")
for item in items:
    print(f"{item.kind.value}: {item.text[:60]}...")
```

## Session JSONL format

Each line is a JSON object with at minimum `id`, `type`, and either
`text` or `content`:

```json
{"id": "u1", "type": "user_turn", "text": "Search for open invoices"}
{"id": "tc1", "type": "tool_call", "text": "invoices.search(status='open')", "parent_id": "u1"}
{"id": "tr1", "type": "tool_result", "content": "...", "parent_id": "tc1"}
```

See `examples/data/mcp_session.jsonl` for a complete example.

## End-to-end example

```python
from contextweaver.adapters.mcp import (
    load_mcp_session_jsonl,
    mcp_tool_to_selectable,
)
from contextweaver.context.manager import ContextManager
from contextweaver.types import ItemKind, Phase

# Load session events
items = load_mcp_session_jsonl("examples/data/mcp_session.jsonl")

# Build context with firewall
mgr = ContextManager()
for item in items:
    if item.kind == ItemKind.tool_result and len(item.text) > 2000:
        mgr.ingest_tool_result(
            tool_call_id=item.parent_id or item.id,
            raw_output=item.text,
            tool_name="mcp_tool",
        )
    else:
        mgr.ingest(item)

pack = mgr.build_sync(phase=Phase.answer, query="invoice status")
print(pack.prompt)
```

See `examples/mcp_adapter_demo.py` for the full runnable demo.

## Prompt-caching compatibility

Anthropic (90%), OpenAI (50%), and Google (75%) all discount the prompt-token
cost of tool definitions when the same prefix is reused across requests.
contextweaver's
[`make_choice_cards`](../src/contextweaver/routing/cards.py) function is
**deterministic and byte-stable** for identical inputs (sorted descending by
score, ascending by `id` for ties — see issue #218 for the regression test
that locks this guarantee), so the cards array your downstream prompt
assembler renders is suitable for placement *before* a cache breakpoint.

The repo guarantees this via `tests/test_cards.py::test_make_choice_cards_byte_identical_stable_order`,
which asserts `bytes(card1) == bytes(card2)` across two consecutive calls
on identical inputs. The invariant survives across the full
`SelectableItem → ChoiceCard → cache prefix` chain.

### Worked example: Anthropic `cache_control`

> **Illustrative — requires the Anthropic SDK.** This snippet imports
> `anthropic` to show how the byte-stable cards array slots into the
> provider's cache-control API. contextweaver itself does not depend on
> the Anthropic SDK; install it separately with `pip install anthropic`
> to run the example as-is, or read it as a pattern reference.

```python
import anthropic  # pip install anthropic
from contextweaver.routing.cards import make_choice_cards
from contextweaver.routing.catalog import Catalog

catalog = Catalog()  # populated elsewhere with stable IDs
cards = make_choice_cards(
    catalog.all(),
    scores={item.id: 0.5 for item in catalog.all()},   # deterministic scoring
    max_cards=20,
)

# Render cards into Anthropic's `tools` array (cacheable prefix).
tools = [
    {
        "name": c.name,
        "description": c.description,
        "input_schema": {"type": "object"},  # hydrate per-call when selected
    }
    for c in cards
]

# Place the cache breakpoint on the LAST tool definition. As long as the
# `cards` array is stable, every request reuses the cache prefix and only
# the trailing user turn varies.
if tools:
    tools[-1]["cache_control"] = {"type": "ephemeral"}

client = anthropic.Anthropic()
client.messages.create(
    model="claude-3-5-sonnet-latest",
    max_tokens=1024,
    tools=tools,
    messages=[{"role": "user", "content": "..."}],
)
```

> **Practical guidance for multi-turn navigation.** When the cards array
> *naturally* changes between turns (e.g., user navigated into a sub-tree),
> the cache prefix invalidates — that's expected. To keep the prefix stable
> across navigation, sort hydrated cards by ID once and append newly-discovered
> cards after the breakpoint. The
> [Webfuse MCP cheat sheet](https://www.webfuse.com/mcp-cheat-sheet)
> documents the canonical "append after cache breakpoint" pattern.
>
> **First-class flag:** `ProxyRuntime(cache_stable=True)` implements this
> pattern automatically — see [gateway spec §5](gateway_spec.md#5-cache-stable-tool-browsing-cache_stabletrue).
> Browsed/hydrated tool ids are tracked per session; on each
> `tool_browse` call, previously-seen cards are emitted first in
> ascending-`id` order, followed by a `__cache_breakpoint__` marker
> card, followed by newly-discovered cards (also `id`-ascending).
> First-sighting card content is frozen, so the prefix bytes are
> stable across browses with different queries. **Caveat:** the
> first emitted card is not the highest-ranked when this flag is on
> — read rank from `ChoiceCard.score`.

## Security Considerations

### MCP annotations are untrusted hints

MCP tool annotations — `readOnlyHint`, `destructiveHint`, `costHint` — are
**server-declared metadata**, not verified security properties.  The
[MCP specification](https://modelcontextprotocol.io/legacy/concepts/tools)
explicitly states:

> _"Clients SHOULD NOT make security-critical decisions based solely on tool
> annotations. Annotations are informational metadata, not security controls."_

contextweaver maps these hints to informational fields on `SelectableItem`:

| Annotation       | Field mapped to             | Purpose               |
|------------------|-----------------------------|-----------------------|
| `readOnlyHint`   | `side_effects=False`, tag `"read-only"` | Routing UX display |
| `destructiveHint`| tag `"destructive"`         | Routing UX display    |
| `costHint`       | `cost_hint` (float)         | Routing cost scoring  |

### `side_effects` is informational only

`item.side_effects = False` (derived from `readOnlyHint=True`) means the
**server advertised** the tool as read-only.  It does **not** guarantee the
tool has no side effects.  A malicious or misconfigured MCP server could
declare `readOnlyHint: True` on a destructive tool; contextweaver would
faithfully tag it `"read-only"` with `side_effects=False`.

**Do not build access-control or safety-gate logic on these fields.**

### Authorization status

contextweaver does **not currently provide an authorization mechanism** for
MCP tools. Do not rely on server-declared annotation hints for access
control.

`CapabilityToken` (see
[issue #20](https://github.com/dgenio/contextweaver/issues/20)) is a
proposed/future feature, not a type that is implemented in the library
today. For actual access control, enforce authorization in your own
application or policy layer.

---

## Runtime modes: transparent proxy and two-tool gateway

The MCP adapter ships two runtime modes for fronting one or more
upstream MCP servers.  Both share the
[`ProxyRuntime`](../src/contextweaver/adapters/proxy_runtime.py) core and
satisfy the contracts in [`docs/gateway_spec.md`](gateway_spec.md):

Production MCP gateway deployments commonly transform raw
user input into routing-oriented queries before calling
`Router.route(query)`. ContextWeaver does not require a
specific rewriting strategy and accepts whichever
routing-shaped query your gateway produces.

| Mode | Discovery channel | Invocation channel | Schema exposure |
|------|-------------------|--------------------|-----------------|
| `ExposureMode.TRANSPARENT` (#13) | Stripped `tools/list` — one entry per upstream tool with sentinel `inputSchema: {"type": "object"}` | `tool_hydrate(tool_id)` + `tool_execute(tool_id, args)` | On demand via `tool_hydrate` |
| `ExposureMode.GATEWAY` (#28 + #34) | None — the agent never sees a `tools/list` | `tool_browse(query|path)` + `tool_execute(tool_id, args)` + `tool_view(handle, selector)` | Internal: `tool_execute` hydrates and validates before upstream dispatch |

Both modes share the same invocation contract: arguments to
`tool_execute` are validated against the hydrated schema via
`jsonschema` before any upstream call, per
[`gateway_spec.md` §4.4](gateway_spec.md#4-schema-exposure-strategy).

### Wiring a gateway over stdio

```python
import asyncio
from contextweaver.adapters import ProxyRuntime, StubUpstream
from contextweaver.adapters.mcp_gateway_server import McpGatewayServer

runtime = ProxyRuntime(StubUpstream([...]))
await runtime.refresh_catalog()
server = McpGatewayServer(runtime, name="example-gateway")
asyncio.run(server.run_stdio())
```

### Wiring a transparent proxy over stdio

```python
from contextweaver.adapters import ExposureMode, ProxyRuntime, StubUpstream
from contextweaver.adapters.mcp_proxy_server import McpProxyServer

runtime = ProxyRuntime(StubUpstream([...]), mode=ExposureMode.TRANSPARENT)
await runtime.refresh_catalog()
server = McpProxyServer(runtime, name="example-proxy")
asyncio.run(server.run_stdio())
```

### Connecting to real upstream MCP servers

Swap [`StubUpstream`](../src/contextweaver/adapters/mcp_upstream.py) for
`McpClientUpstream(session)` (one upstream) or
`MultiplexUpstream([a, b, ...])` (multi-server fan-out).  The runtime
itself is transport-agnostic; the upstream adapter handles the wire
protocol.

### Error shape

Every gateway / proxy meta-tool returns either a `ResultEnvelope` or a
typed
[`GatewayError`](../src/contextweaver/adapters/gateway_error.py)
matching `gateway_spec.md` §3.4:

```json
{
  "error": "PATH_INVALID" | "PATH_NOT_FOUND" | "ARGS_INVALID" | "UPSTREAM_ERROR" | "HYDRATE_FAILED" | "VIEW_FAILED",
  "message": "<human-readable>",
  "path": "<offending path or tool_id>",
  "details": { "...": "..." }
}
```

The meta-tools never raise across the MCP boundary — failures are
delivered as `isError=true` `CallToolResult` payloads.

### See also

- [`docs/gateway_spec.md`](gateway_spec.md) — the normative
  surface specification.
- [`examples/mcp_gateway_demo.py`](../examples/mcp_gateway_demo.py) —
  end-to-end gateway flow using `StubUpstream`.
- [`examples/mcp_proxy_demo.py`](../examples/mcp_proxy_demo.py) —
  end-to-end proxy flow.
- [Recipes > Claude Desktop](recipes/claude_desktop.md) — put a
  contextweaver gateway in front of Claude Desktop's MCP client.
- [Recipes > GitHub Copilot](recipes/github_copilot.md) — put a
  contextweaver gateway in front of VS Code Copilot Chat (agent mode).
- [`examples/architectures/mcp_context_gateway/main_real.py`](../examples/architectures/mcp_context_gateway/main_real.py)
  — the reference architecture run against verbatim `tools/list`
  snapshots of MIT-licensed reference MCP servers.

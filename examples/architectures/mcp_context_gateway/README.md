# MCP Context Gateway — reference architecture

> A "DevOps Copilot" agent fronting a 60-tool MCP-style gateway. Demonstrates
> the contextweaver launch narrative end-to-end: a large tool catalog, compact
> `ChoiceCards`, lazy schema hydration, the context firewall on a large
> upstream result, and an answer-phase prompt that sees only a summary and
> an artifact handle — never the raw rowset.

> **This example simulates the MCP gateway flow using contextweaver
> primitives.** It does not connect to a real MCP server. The same
> `ContextManager.ingest_mcp_result` and `Router` APIs used here are the
> APIs you wire to a live `contextweaver.adapters.ProxyRuntime` in
> production. See `docs/integration_mcp.md` and `docs/gateway_spec.md`
> for the real-gateway integration.

## What problem this demonstrates

A real agent fronting an MCP-style gateway with dozens of tools faces three
pressures at once:

1. **Catalog bloat** — dumping every tool schema costs ~10 KB per tool and
   degrades model focus.
2. **Result bloat** — a single BigQuery query can return 15+ KB of rows the
   model only needs a summary of.
3. **Dependency loss** — naive truncation loses the link between the tool
   call and the result it produced.

This example exercises contextweaver's response to all three in one
scripted turn:

| Pressure | contextweaver primitive | Outcome in this run |
|---|---|---|
| Catalog bloat | `Router` + `make_choice_cards` | 60 tools → **5 ChoiceCards** in the prompt |
| Result bloat | `ContextManager.ingest_mcp_result` | 16,507 chars → **194-char** summary + artifact |
| Dependency loss | `parent_id` chains + dependency closure | The `[TOOL CALL]` and `[TOOL RESULT]` stay paired in the final prompt |

## Variants

This architecture ships three sibling runs against the same packaged
60-tool catalog (`contextweaver.data.mcp_gateway_catalog.yaml`):

| File | What it shows | When to read it |
|---|---|---|
| `main.py` | Single-turn narrative inlining `Router`, `Catalog.hydrate`, and `ContextManager.ingest_mcp_result`. | First read — easiest to follow. |
| `main_live.py` (issue #260) | Same scenario driven through `tool_browse` / `tool_execute` / `tool_view` on a `ProxyRuntime` + `StubUpstream`. Exercises the real MCP wire shape. | Reference for `contextweaver mcp serve --gateway --catalog ...`. |
| `main_multi.py` (issue #262) | Five-turn transcript with cross-turn fact accumulation and a Slack-thread firewall hit on Turn 5. | When you want to see whether facts survive across turns. |

All three are wired into `make architectures`.

The catalog itself ships inside the wheel via `contextweaver.data.gateway_catalog_path()` — that's why these example scripts work from a `pip install contextweaver` without the `examples/` directory.

## How to run it

```bash
python examples/architectures/mcp_context_gateway/main.py
```

Or via `make architectures` / `make example`.

The script is deterministic (fixed seeds, no network, no LLM) — identical
inputs produce identical outputs. A captured run is in [`OUTPUT.md`](OUTPUT.md).

## Expected output

Skim [`OUTPUT.md`](OUTPUT.md) for the full transcript. The key metrics block
at the end summarises:

```text
catalog_tools           = 60
exposed_choice_cards    = 5
hydrated_schema_chars   = 854   (selected tool only)
raw_result_chars        = 16,507
injected_summary_chars  = 194
firewall_reduction_pct  = 98.8 %
artifact_handle         = artifact:result:tc1
final_prompt_tokens     = 120
final_prompt_chars      = 645
```

## What to look for

When you read the run, focus on these moments:

1. **Five ChoiceCards, no schemas.** Step `[1/5]` prints the rendered card
   text the model would see at the route phase. It is 536 chars total —
   pure description + tags + score, no JSON Schemas.
2. **Schema hydration is lazy.** Step `[2/5]` prints `854 chars` for the
   selected tool and `0 chars` for the other 59. That zero is the point.
3. **The firewall is the only bulk reducer.** Step `[3/5]–[4/5]` shows the
   16,507-char raw result collapsing to a 194-char summary. The full body
   lives in the artifact store at `artifact:result:tc1`.
4. **The final prompt contains the dependency chain.** Step `[5/5]` prints
   the answer-phase prompt. It contains the user turn, the tool call, the
   firewall summary, the durable fact, and the artifact handle — but **not**
   the rowset. The regression check pins this with the sentinel
   `"mrr_delta_usd": -450`, which only appears in day-47's row (deep in the
   raw data, never near a summary line).

## How this maps to a real MCP / tool-heavy agent runtime

| Mocked here | Real MCP runtime equivalent |
|---|---|
| `load_catalog_yaml("catalog.yaml")` | `ProxyRuntime.register_tool_defs_sync(upstream.list_tools())` — the gateway pulls tool defs from upstream servers. |
| `Router.route(query)` | The agent's `tool_browse(query)` meta-tool call returns the same `ChoiceCard` shape (see `examples/mcp_gateway_demo.py`). |
| `SchemaSource.from_json_file("tool_schemas.json")` + `hydrate_with_schema(catalog, chosen, schemas)` | The gateway hydrates the selected tool's input schema *only* when the agent calls `tool_execute(tool_id, args)`. The same `routing.hydration` helpers work over `ProxyRuntime`'s upstream tool list — the example loads schemas from the sidecar JSON, production loads them from `runtime.list_tool_defs()`. |
| `_mock_bigquery_result()` | Whatever the upstream MCP server returns over stdio / HTTP. |
| `ContextManager.ingest_mcp_result(...)` | **Identical API — no swap needed.** This is the production-shape method that parses the MCP wire result, stores binary content as artifacts, and runs the firewall. |
| `ContextManager.add_fact_sync(...)` | Same — facts persist across turns regardless of how the result was sourced. |

So the swap from this example to a real MCP gateway is mostly replacing the
catalog-load step (YAML → upstream tool list) and the tool-call step (mock
function → `await runtime.tool_execute(...)`). Everything else — the
routing, the firewall, the answer-phase build — is already production code.

## Limitations

- **No real MCP transport.** The example imports `ContextManager` directly;
  it does not spin up a `mcp.server.Server` over stdio. For the live
  transport, see `src/contextweaver/adapters/mcp_gateway_server.py` and
  `examples/mcp_gateway_demo.py`.
- **The intent map is explicit.** A production agent would either run an
  LLM over the rendered ChoiceCards (and pick the tool from that prompt) or
  use deterministic intent classification. This script holds the intent
  literal (`SELECTED_TOOL_ID = "bigquery.run_query"`) so the routing
  outcome is testable without involving an LLM.
- **One turn only.** A real session would route per-turn, accumulate facts,
  and rebuild the answer prompt each time. The multi-turn shape lives in
  the [Slack ops bot](../slack_ops_bot/) architecture; this example focuses
  on the single-call MCP gateway shape.
- **`tiktoken` cache.** When run in an environment that cannot reach the
  `tiktoken` CDN, the script falls back to a chars-÷-4 token estimate (a
  `BuildStats` warning makes this explicit). The metric in `OUTPUT.md`
  reflects that fallback.

## Follow-up architectures

Tracked under issue #198 and the launch issues #243 (`contextweaver mcp
serve` CLI surface) and #246 (the same as a wrapped subcommand). When
those land, this example will gain a *Run against a live MCP server*
appendix using the same primitives.

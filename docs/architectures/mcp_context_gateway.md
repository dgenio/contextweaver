# MCP Context Gateway

> Reference architecture for a "DevOps Copilot" agent fronting a 60-tool
> MCP-style gateway. Demonstrates the contextweaver launch narrative
> end-to-end: a large tool catalog, compact `ChoiceCards`, lazy schema
> hydration, the context firewall on a large upstream result, and an
> answer-phase prompt that sees only a summary and an artifact handle —
> never the raw rowset.

!!! info "Simulated, not connected"
    This example simulates the MCP gateway flow using contextweaver
    primitives. It does not connect to a real MCP server. The same
    `ContextManager.ingest_mcp_result` and `Router` APIs used here are
    the APIs you wire to a live `ProxyRuntime` in production —
    see [MCP Integration](../integration_mcp.md) and
    [Gateway Spec](../gateway_spec.md).

## TL;DR

| What | Where |
|---|---|
| The script | [`examples/architectures/mcp_context_gateway/main.py`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/mcp_context_gateway/main.py) |
| The catalog | [`examples/architectures/mcp_context_gateway/catalog.yaml`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/mcp_context_gateway/catalog.yaml) |
| Captured output | [`examples/architectures/mcp_context_gateway/OUTPUT.md`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/mcp_context_gateway/OUTPUT.md) |
| Local README | [`examples/architectures/mcp_context_gateway/README.md`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/mcp_context_gateway/README.md) |

### Variants

| Variant | Script | What it adds |
|---|---|---|
| Single-turn (canonical) | [`main.py`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/mcp_context_gateway/main.py) | The reference shape. Uses the public `routing.hydration.SchemaSource` (#261) instead of a hand-rolled `_FULL_SCHEMAS` dict. |
| Live transport | [`main_live.py`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/mcp_context_gateway/main_live.py) | Same scenario but routed through a real `mcp.server.Server` + `ClientSession` over the in-memory MCP transport (#260). |
| Multi-turn | [`main_multi.py`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/mcp_context_gateway/main_multi.py) | 4-turn transcript (BigQuery → Linear → Slack → PagerDuty) with fact accumulation across turns; turn 1's artifact survives via dependency closure (#262). |
| Real-MCP catalogs | [`main_real.py`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/mcp_context_gateway/main_real.py) | Runs the same shape against committed snapshots of three real public MCP servers (`server-time`, `server-filesystem`, `server-everything`) under [`real_catalogs/`](https://github.com/dgenio/contextweaver/tree/main/examples/architectures/mcp_context_gateway/real_catalogs) (#280). |
| CLI surface | `contextweaver demo --scenario mcp-gateway-full` | The single-turn variant from the CLI without invoking the example script (#264). |
| Benchmark range | [`benchmarks/gateway_scorecard.md`](https://github.com/dgenio/contextweaver/blob/main/benchmarks/gateway_scorecard.md) | Five gateway-shaped scenarios — firewall reduction **0.0 % – 98.8 %**. Regenerate with `make benchmark-gateway && make gateway-scorecard` (#270). |

Run it:

```bash
python examples/architectures/mcp_context_gateway/main.py
```

(Or `make architectures` / `make example`.)

## The shape

| Step | contextweaver primitive | What happens |
|---|---|---|
| 1. Load catalog | `load_catalog_yaml` + `Catalog.register` | 60 mocked tools across 10 namespaces are registered. |
| 2. Route | `Router.route(query)` + `make_choice_cards` | The 60-tool catalog is narrowed to **5 compact `ChoiceCard`s** (536 chars total, no schemas). |
| 3. Hydrate schema | Explicit lookup in `_FULL_SCHEMAS` | Only the chosen tool's JSON Schema (~854 chars) is materialised. The other 59 stay at zero bytes. |
| 4. Call upstream | Mock function returns MCP wire shape `{"content": [...], "isError": False}` | A ~16 KB rowset comes back. |
| 5. Firewall | `ContextManager.ingest_mcp_result` | Raw 16,507 chars → 194-char summary + extracted facts + artifact `artifact:result:tc1`. The full bytes live in the artifact store. |
| 6. Persist fact | `ContextManager.add_fact_sync` | A durable summary (`growth -> starter ...`) is written so the answer prompt doesn't need to re-read the rowset. |
| 7. Answer | `ContextManager.build_sync(phase=Phase.answer, ...)` | A 142-token, 645-char final prompt with `[FACTS]`, `[TOOL RESULT]` (summary only), `[USER]`, `[TOOL CALL]`. **No rowset.** |

## Captured metrics

From the deterministic run (see [`OUTPUT.md`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/mcp_context_gateway/OUTPUT.md)):

| Metric | Value |
|---|---:|
| `catalog_tools` | 60 |
| `exposed_choice_cards` | 5 |
| `hydrated_schema_chars` | 854 |
| `raw_result_chars` | 16,507 |
| `injected_summary_chars` | 194 |
| `firewall_reduction_pct` | **98.8 %** |
| `final_prompt_tokens` | 142 |
| `final_prompt_chars` | 645 |

## How this maps to a real MCP runtime

The swap from this example to a real MCP gateway is mostly two changes:

1. Replace `load_catalog_yaml(...)` with
   `ProxyRuntime.register_tool_defs_sync(upstream.list_tools())` — the
   gateway pulls tool defs from upstream MCP servers.
2. Replace the mock function with `await runtime.tool_execute(tool_id, args)`,
   which delegates to the upstream over the MCP transport.

`ContextManager.ingest_mcp_result(...)`, `Router.route(...)`,
`make_choice_cards(...)`, and the answer-phase build are **identical** in
both setups. See [`examples/mcp_gateway_demo.py`](https://github.com/dgenio/contextweaver/blob/main/examples/mcp_gateway_demo.py)
for the live-transport variant using `ProxyRuntime` + `StubUpstream`.

## Pinned invariants

[`tests/test_architectures_mcp_context_gateway.py`](https://github.com/dgenio/contextweaver/blob/main/tests/test_architectures_mcp_context_gateway.py)
runs `main()` and asserts:

- Catalog loads exactly 60 tools / 10 namespaces.
- The route phase shortlists to 5 cards out of 60.
- ChoiceCards rendered to the model do **not** contain any `"inputSchema"`
  or `"properties"` strings.
- Only `bigquery.run_query` has its schema hydrated; the other 59 are zero
  bytes.
- The raw result is > 10 KB; the firewall summary is < 500 chars.
- The sentinel `"mrr_delta_usd": -450` (only in day-47's row, deep in the
  rowset) is **not** present in the final answer-phase prompt.
- Every documented metric (`catalog_tools`, `exposed_choice_cards`,
  `hydrated_schema_chars`, `raw_result_chars`, `injected_summary_chars`,
  `firewall_reduction_pct`, `artifact_handle`, `final_prompt_tokens`,
  `final_prompt_chars`) is present in stdout.

A failure on any of these is a regression in the load-bearing launch
narrative.

## Limitations

- No live MCP transport. Use `examples/mcp_gateway_demo.py` for that.
- Single turn — for multi-turn investigations with fact accumulation see
  [Slack ops bot](slack_ops_bot.md).
- The intent map is held explicit so the routing outcome is testable
  without involving an LLM.

## Follow-ups

Tracked under issues [#243](https://github.com/dgenio/contextweaver/issues/243)
and [#246](https://github.com/dgenio/contextweaver/issues/246) — when the
`contextweaver mcp serve` CLI lands, this example gains a *"Run against a
live MCP server"* appendix using the same `ingest_mcp_result` /
`Router.route` calls shown here.

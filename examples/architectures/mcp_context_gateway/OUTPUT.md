# MCP Context Gateway — captured run

> Captured by running `python examples/architectures/mcp_context_gateway/main.py`
> from a clean checkout. The run is deterministic (fixed seeds, no network,
> no LLM, no real MCP server) so identical inputs produce identical outputs.
> A diff against this file is a regression signal — see
> `tests/test_architectures_mcp_context_gateway.py` for the pinned invariants.

```text
============================================================================
contextweaver -- MCP Context Gateway reference architecture
============================================================================
(simulated MCP gateway flow using contextweaver primitives)

Loaded catalog: 60 tools across 10 namespaces

============================================================================
[1/5] Route phase — model sees compact ChoiceCards, NOT schemas
============================================================================
user typed:    "Why did customer C-12345's MRR drop last month?"
routing query: 'Execute a BigQuery query to find MRR delta rows for customer C-12345'
(a production agent would LLM-rephrase the user question into the routing query;
 here we hold both explicit so the demo is deterministic.)
shortlist (5 of 60): ['bigquery.run_query', 'bigquery.dry_run', 'analytics.events.query', 'bigquery.list_datasets', 'analytics.dashboards.get']
chosen:    bigquery.run_query  (intent='bigquery.run_query')

ChoiceCards rendered to the model (536 chars, NO full schemas):
[1/5] bigquery.run_query (tool) — Execute a BigQuery SQL query and return rows [bigquery, read, sql] score=2.67
[2/5] bigquery.dry_run (tool) — Estimate cost of a BigQuery query [bigquery, plan, sql] score=1.65
[3/5] analytics.events.query (tool) — Query the events table for a customer [analytics, events, read] score=1.21
[4/5] bigquery.list_datasets (tool) — List BigQuery datasets in a project [bigquery, schema] score=1.17
[5/5] analytics.dashboards.get (tool) — Fetch a saved analytics dashboard [analytics, dashboards] score=0.00

============================================================================
[2/5] Call phase — hydrate ONLY the selected tool's schema
============================================================================
tool: bigquery.run_query
hydrated schema for: 'bigquery.run_query'  (854 chars)
hydrated schema for the other 59 tools: 0 chars (skipped)

Schema preview (first 200 chars):
  '{\n  "type": "object",\n  "title": "bigquery_run_query",\n  "properties": {\n    "sql": {\n      "type": "string",\n      "description": "Standard-SQL BigQuery query."\n    },\n    "project": {\n      "type": '
  ...

============================================================================
[3/5] Tool call + [4/5] context firewall
============================================================================
called: bigquery.run_query(...)
raw upstream result: 16,507 chars (MCP wire shape)
firewall: 16,507 chars  ->  194-char summary  (artifact artifact:result:tc1)
extracted facts (first 3 of 24):
  - rowset: bigquery.run_query
  - project: ops-analytics-prod
  - rows_returned: 90

============================================================================
[5/5] Answer phase — final prompt sees summary + handle, NOT raw rows
============================================================================
answer prompt: included=3  tokens=142
final prompt length: 645 chars
contains raw rowset? no
contains artifact handle? yes
contains durable fact?    yes
contains user query?      yes
contains tool call?       yes

--- Final answer-phase prompt ---
[FACTS]
- customer.C-12345.plan_change: growth -> starter (self-serve, day 47, -$450 MRR)

[TOOL RESULT [artifact:artifact:result:tc1]]
rowset: bigquery.run_query
project: ops-analytics-prod
rows_returned: 90
schema: date STRING, customer_id STRING, plan STRING, mrr_delta_usd INT64, reason_code STRING, actor STRING, notes STRING

[USER]
Why did customer C-12345's MRR drop last month?

[TOOL CALL]
bigquery.run_query({"sql":"SELECT date, plan, mrr_delta_usd, reason_code, actor, notes FROM `ops-analytics-prod.billing.mrr_changes` WHERE customer_id = 'C-12345' AND date BETWEEN '2026-02-01' AND '2026-04-30' ORDER BY date","max_results":1000})
--- end prompt ---

============================================================================
Metrics summary
============================================================================
catalog_tools           = 60
exposed_choice_cards    = 5
hydrated_schema_chars   = 854  (selected tool only)
raw_result_chars        = 16,507
injected_summary_chars  = 194
firewall_reduction_pct  = 98.8%
artifact_handle         = artifact:result:tc1
final_prompt_tokens     = 142
final_prompt_chars      = 645
```

## What the captured numbers tell you

| Metric | Value | What it shows |
|---|---:|---|
| `catalog_tools` | **60** | The pool the agent could pick from. |
| `exposed_choice_cards` | **5** | What the model actually sees at the route phase — 92 % of the catalog never enters the prompt. |
| `hydrated_schema_chars` | **854** | Full JSON Schema for the one selected tool. Hydration is lazy: zero schema bytes for the other 59. |
| `raw_result_chars` | **16,507** | The upstream BigQuery rowset, MCP wire shape. Roughly 90 daily rows, 24 extracted facts. |
| `injected_summary_chars` | **194** | What the firewall leaves on the prompt side — header lines + row count + schema, no rows. |
| `firewall_reduction_pct` | **98.8 %** | (1 − 194 / 16,507) × 100. The raw rowset never reaches the LLM. |
| `artifact_handle` | `artifact:result:tc1` | The full 16 KB body is in the artifact store, addressable via `tool_view(handle, selector=…)`. |
| `final_prompt_tokens` | **142** | Total answer-phase prompt budget consumed. Compare against the 4,000-token answer budget configured in `main.py`. |
| `contains raw rowset?` | **no** | Pinned by `tests/test_architectures_mcp_context_gateway.py` — the sentinel `"mrr_delta_usd": -450` (only in day 47's row) must not appear in the prompt. |

The takeaway: a 16 KB tool result and a 60-tool catalog collapse into a
645-char, 120-token final prompt without losing the information the agent
needs to answer the user's question.

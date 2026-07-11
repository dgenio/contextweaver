# SLO Starter Kit

> A starting point for putting the contextweaver gateway under SLOs: SLI
> definitions grounded in the diagnostics it actually emits, two example SLOs
> with burn-rate alert templates, and a dashboard skeleton.

**What contextweaver ships — and what it does not.** The gateway emits
structured JSONL diagnostics (`contextweaver mcp serve --diagnostics FILE`,
one `DiagnosticEvent` per line — see [Telemetry](telemetry.md)). It does
**not** ship a Prometheus exporter, alert rules, or a Grafana integration.
Everything below the SLI section is a *template*: you map the JSONL stream
onto metrics with your own pipeline (vector/fluent-bit/promtail + your TSDB),
then adapt the queries to your naming.

## What the events carry

Every event has `event`, `timestamp`, `success`, `duration_ms`, `session_id`,
and optional `tool_id` / `namespace`, plus payload-safe `attributes` (sizes,
counts, error codes — never argument values or result text). The families you
will alert on:

| Event | Emitted when | SLI-relevant fields |
|---|---|---|
| `execute.completed` / `execute.failed` | Every `tool_execute` dispatch | `success`, `duration_ms`, `attributes.raw_tokens`, `attributes.compact_tokens`, `attributes.tokens_saved`, `attributes.artifact_bytes`, `attributes.firewall_triggered`, `attributes.error_code` |
| `browse.completed` / `browse.failed` | Every `tool_browse` | `success`, `duration_ms`, `attributes.card_count`, `attributes.schema_tokens_avoided` |
| `hydrate.completed` / `hydrate.failed` | Schema hydration | `success`, `duration_ms` |
| `view.completed` / `view.failed` | Artifact drill-down | `success`, `duration_ms` |
| `catalog.loaded` | Catalog registration / refresh | `timestamp`, catalog size and exposure attributes |

## SLI definitions

**1. Availability — execute success rate.** Fraction of `tool_execute` calls
that succeed: `execute.completed` events with `success: true` over all
`execute.completed` + `execute.failed` events. `execute.failed` carries
`attributes.error_code`, so you can exclude caller errors (e.g.
`SCHEMA_MISMATCH`, `POLICY_DENIED`) from the error budget if your policy says
those are not the gateway's fault.

**2. Latency — execute/browse p95.** 95th percentile of `duration_ms` on
`execute.completed` and `browse.completed`. `duration_ms` is the gateway-side
dispatch latency (includes upstream time). Offline, the same number comes from
`contextweaver mcp stats --diagnostics FILE` (`latency_ms.p95`).

**3. Firewall efficacy — tokens kept out of prompts.** From
`execute.completed` attributes: `sum(tokens_saved) / sum(raw_tokens)`. A
falling ratio means large results are reaching the model inline. The events
also carry `attributes.artifact_bytes` (bytes offloaded to the artifact store)
and `attributes.firewall_triggered` if you prefer a byte- or count-based view.
Only these carried attributes are measurable — there is no "would-have-been"
token count beyond `raw_tokens`.

**4. Catalog freshness.** Age of the newest `catalog.loaded` event. A stale
catalog means refresh is failing (or the gateway restarted without one) and
routing is serving old tools.

## Example SLOs

These use a **metric naming convention** for the JSONL→metrics mapping; your
pipeline must produce these (or equivalents) before any query below works:

- `cw_execute_total{success="true"|"false"}` — counter per execute event
- `cw_execute_duration_ms_bucket` / `_count` — histogram of `duration_ms`
  (`cw_browse_duration_ms_bucket` likewise for browse)
- `cw_tokens_raw_total`, `cw_tokens_saved_total` — counters from attributes
- `cw_catalog_loaded_timestamp_seconds` — gauge set to each
  `catalog.loaded` event's timestamp

**SLO 1 — availability:** 99.5% of execute calls succeed, 30-day window
(error budget 0.5%). Multiwindow burn-rate alerts, PromQL-style pseudo-queries:

```text
# page: 14.4x burn (budget gone in ~2 days), 1h + 5m windows both burning
(sum(rate(cw_execute_total{success="false"}[1h]))  / sum(rate(cw_execute_total[1h])))  > 14.4 * 0.005
and
(sum(rate(cw_execute_total{success="false"}[5m]))  / sum(rate(cw_execute_total[5m])))  > 14.4 * 0.005

# ticket: 6x burn (budget gone in ~5 days), 6h + 1h windows
(sum(rate(cw_execute_total{success="false"}[6h]))  / sum(rate(cw_execute_total[6h])))  > 6 * 0.005
and
(sum(rate(cw_execute_total{success="false"}[1h]))  / sum(rate(cw_execute_total[1h])))  > 6 * 0.005
```

**SLO 2 — latency:** 95% of execute calls complete under 2000 ms, 30-day
window (slow budget 5%):

```text
# ticket: 6x burn on the slow-call budget over 1h
1 - (sum(rate(cw_execute_duration_ms_bucket{le="2000"}[1h]))
       / sum(rate(cw_execute_duration_ms_count[1h]))) > 6 * 0.05
```

Copyable rule-file versions of both live in
[`examples/slo/burn_rate_rules.yaml`](https://github.com/dgenio/contextweaver/blob/main/examples/slo/burn_rate_rules.yaml).
Pick thresholds from your own baseline (`mcp stats --diagnostics FILE` prints
current p50/p95/max), not from these placeholders.

## Dashboard skeleton

One panel per SLI; the datasource is an honest placeholder — point it at
wherever your pipeline lands the mapped metrics. Full template:
[`examples/slo/dashboard.json`](https://github.com/dgenio/contextweaver/blob/main/examples/slo/dashboard.json).

```json
{
  "title": "contextweaver gateway SLIs",
  "templating": {"list": [{"name": "datasource", "type": "datasource", "query": "prometheus"}]},
  "panels": [
    {"type": "stat", "title": "Availability - execute success rate",
     "datasource": {"uid": "${datasource}"},
     "targets": [{"expr": "sum(rate(cw_execute_total{success=\"true\"}[1h])) / sum(rate(cw_execute_total[1h]))"}]},
    {"type": "timeseries", "title": "Latency - execute p95 (ms)",
     "datasource": {"uid": "${datasource}"},
     "targets": [{"expr": "histogram_quantile(0.95, sum by (le) (rate(cw_execute_duration_ms_bucket[5m])))"}]},
    {"type": "stat", "title": "Firewall efficacy",
     "datasource": {"uid": "${datasource}"},
     "targets": [{"expr": "sum(rate(cw_tokens_saved_total[1h])) / sum(rate(cw_tokens_raw_total[1h]))"}]},
    {"type": "stat", "title": "Catalog freshness (s)",
     "datasource": {"uid": "${datasource}"},
     "targets": [{"expr": "time() - max(cw_catalog_loaded_timestamp_seconds)"}]}
  ]
}
```

## Getting the data out

```bash
contextweaver mcp serve --catalog tools.yaml --diagnostics /var/log/contextweaver/diag.jsonl
```

Ship `diag.jsonl` with your log agent, map events to the metric names above,
load the rule and dashboard templates, and revise the targets after a week of
real traffic. For ad-hoc inspection without a pipeline,
`contextweaver mcp stats --diagnostics FILE` renders the same counts, savings,
and latency percentiles offline.

# Telemetry Handoff Contract (v1)

The gateway emits versioned `DiagnosticEvent` records (from
`contextweaver.diagnostics`) to a pluggable sink; `contextweaver mcp serve
--diagnostics FILE` appends them as JSONL. This page freezes that stream as a
**consumable contract** (issue #382) so downstream tooling — ChainWeaver
dashboards, log pipelines, offline analysis — can depend on it without
importing contextweaver internals. The programmatic surface lives in
`contextweaver.telemetry_contract`.

## The envelope

Every line is one JSON object validating against the published schema
[`schemas/telemetry/v1/diagnostic_event.schema.json`](https://github.com/dgenio/contextweaver/blob/main/schemas/telemetry/v1/diagnostic_event.schema.json)
(JSON Schema Draft 2020-12).

| Field | Type | Notes |
| --- | --- | --- |
| `version` | integer | Envelope version; always `1` for this contract. **Required.** |
| `event` | string | Stable dot-separated name, e.g. `execute.completed`. **Required.** |
| `timestamp` | string | UTC ISO-8601. **Required.** |
| `success` | boolean | Whether the operation succeeded. **Required.** |
| `session_id` | string | Correlation key (see below). **Required.** |
| `duration_ms` | number \| null | Operation latency, when applicable. |
| `tool_id` | string \| null | Canonical tool id, when applicable. |
| `namespace` | string \| null | Tool namespace, when applicable. |
| `attributes` | object | Event-specific metadata; open (`additionalProperties: true`). |

## Event families

`EVENT_FAMILIES` maps the eight contract families to event-name prefixes;
`classify_event()` applies the mapping deterministically (longest prefix
wins). Families marked *reserved* have a prefix allocated but no dedicated
emitter yet — the honest current location of their data is noted.

| Family | Prefix(es) | Status | Emitted today by |
| --- | --- | --- | --- |
| `catalog_inventory` | `catalog.` | live | `catalog.loaded` — catalog size and static schema exposure. |
| `route_request` | `browse.` | live | `browse.completed` / `browse.failed` — one routed browse request. |
| `shortlist` | `shortlist.` | reserved | Shortlist data rides on `browse.completed` attributes (`card_count`, `tool_ids`). |
| `schema_hydration` | `hydrate.` | live | `hydrate.completed` / `hydrate.failed`. |
| `execution` | `execute.` | live | `execute.completed` / `.failed` / `.dry_run` / `.cache_hit`. |
| `firewall_artifact` | `view.` | live | `view.completed` / `view.failed` — artifact drill-down usage. |
| `policy_denial` | `policy.` | reserved | Denials surface as `execute.failed` with `attributes.error_code` of `POLICY_DENIED` / `AUTH_REQUIRED` (issue #373). |
| `visibility` | `visibility.` | reserved | `visibility.denied` is the planned name for the visibility gate (`adapters/gateway_visibility.py`, in progress). |

A committed sample stream covering six families lives at
`tests/fixtures/telemetry_v1_sample.jsonl`.

## Redaction by default

Events are **metadata-only**. Runtime instrumentation records identifiers,
sizes, timings, argument *key names*, and error codes — never query text,
argument values, result text, or artifact bytes. There is nothing to opt out
of: the payload never enters the event. `validate_event_dict()` adds a
defence-in-depth heuristic and flags any attribute value rendering longer
than 2000 characters as likely payload leakage.

## Correlation

All events of one gateway runtime carry the same `session_id`, so a consumer
can reconstruct a session timeline (catalog load → browse → hydrate →
execute → view) by grouping on it. `tool_id` correlates events touching the
same tool across sessions; `attributes.artifact_refs` on `execute.completed`
link forward to the `view.*` events that drill into those artifacts.

## Versioning policy

- **Additive within v1.** New event names, new families, new optional
  envelope fields, and new attribute keys may appear at any time; consumers
  must ignore what they do not recognise (the schema keeps
  `additionalProperties: true`).
- **Breaking means v2.** Removing or retyping a required field, or changing
  the meaning of an existing one, publishes a new
  `schemas/telemetry/v2/` directory and bumps
  `TELEMETRY_CONTRACT_VERSION`; v1 files remain frozen.
- Error codes inside `attributes.error_code` follow the gateway error
  taxonomy in [docs/errors.md](errors.md) and `adapters/gateway_error.py`.

## End-to-end example

Produce a stream by pointing the gateway at a diagnostics file:

```bash
contextweaver mcp serve --catalog tools.yaml --diagnostics /var/log/cw/events.jsonl
```

Consume it downstream — no gateway or MCP dependency needed:

```python
from contextweaver.telemetry_contract import classify_event, read_jsonl

events, problems = read_jsonl("/var/log/cw/events.jsonl")
for problem in problems:  # malformed lines are collected, never raised
    print("skipped:", problem)

by_family: dict[str, int] = {}
for event in events:
    family = classify_event(event) or "unclassified"
    by_family[family] = by_family.get(family, 0) + 1
print(by_family)
```

Non-Python consumers validate each line against the published schema
(`$id`:
`https://github.com/dgenio/contextweaver/schemas/telemetry/v1/diagnostic_event.schema.json`)
and group on the prefixes in the family table above.

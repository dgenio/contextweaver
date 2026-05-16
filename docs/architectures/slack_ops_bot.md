# Slack ops bot

> Production reference architecture for an internal Slack bot fronting ~48
> ops tools. Demonstrates how routing, the context firewall, and persistent
> facts compose end-to-end around a realistic multi-turn investigation.

## TL;DR

| What | Where |
|---|---|
| The script | [`examples/architectures/slack_ops_bot/main.py`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/slack_ops_bot/main.py) |
| The catalog | [`examples/architectures/slack_ops_bot/catalog.yaml`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/slack_ops_bot/catalog.yaml) |
| Captured output | [`examples/architectures/slack_ops_bot/OUTPUT.md`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/slack_ops_bot/OUTPUT.md) |
| Local README | [`examples/architectures/slack_ops_bot/README.md`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/slack_ops_bot/README.md) |

Run it:

```bash
python examples/architectures/slack_ops_bot/main.py
```

(Or `make architectures` / `make example`.)

## The shape

The bot walks a six-turn on-call investigation:

1. *"look up the on-call engineer for api-gateway"* — `oncall.lookup`
2. *"tail the last hour of api-gateway logs"* — `logs.tail` (large output → firewall)
3. *"show api-gateway deploy status"* — `deploy.status`
4. *"roll back the api-gateway deploy to the previous build"* — `deploy.rollback`
5. *"create a new incident ticket for this api-gateway outage"* — `tickets.create`
6. *"show me the on-call schedule for tomorrow"* — `oncall.schedule`

For each turn:

- The [`Router`](../architecture.md#routing-engine) narrows 48 tools to a
  top-3 shortlist (`top_k=3`).
- The bot picks one tool *from the shortlist* using an explicit intent
  map. That separation is the **load-bearing pattern**: contextweaver
  bounds the choice, the bot (or, in production, an LLM) makes the final
  selection.
- The tool is "called" against a mocked backend. Large outputs go through
  the [firewall](../architecture.md#context-firewall) — the 34 KB log
  dump in turn 2 becomes a 501-char summary on the prompt while the raw
  bytes are parked in the artifact store.
- Persistent [facts](../concepts.md) (who's on-call, what just rolled
  back, the new ticket number) are written via
  `ContextManager.add_fact_sync` so they survive into the answer-phase
  prompt for every subsequent turn.

## What is and isn't load-bearing

| contextweaver feature | Used | What it does here |
|---|---|---|
| `Router.route(query)` | ✅ | 48 tools → top-3 shortlist |
| Bounded choice pattern | ✅ | Bot picks from the shortlist, not from the whole catalog |
| `TreeBuilder` DAG | ✅ | One-shot graph build at startup; sub-millisecond routes |
| Context firewall | ✅ | 34 KB log dump → 501-char summary before it touches the prompt |
| `ArtifactStore` | ✅ | Raw log bytes parked out-of-band for later drilldown |
| `FactStore` | ✅ | On-call engineer + incident ticket survive across turns |
| `Phase`-specific budgets | ✅ | Route (1.5K), answer (4K) tight enough that the budget matters |
| Dependency closure | ✅ | Tool calls + tool results stay paired as `select_and_pack` evicts items |
| Drilldown | ❌ | See [cookbook recipe 4](../cookbook.md#4-firewall--drilldown-for-large-tool-outputs) |
| Async build | ❌ | Slack latency is forgiving; the voice-agent architecture exercises this |
| Alternative scorer backends | ❌ | Default TF-IDF works for this catalog; try `bm25` / `fuzzy` on noisier ones |
| Sensitivity enforcement | ❌ | Production bots should set `ContextPolicy.sensitivity_floor` and tag PII |

## How to adapt it

1. Replace `_TOOL_RESPONSES` with calls to your real ops backends.
2. Replace `_select_from_shortlist` with an LLM call that picks from
   `Router.route(...).candidate_ids`.
3. Tighten `ContextBudget` once you measure your prompt sizes.
4. Add a `RedactionHook` if any real tool output carries PII or secrets.
5. Persist `mgr.fact_store` to a durable backend (the in-memory store is
   per-run).

## Follow-ups

The other two reference architectures from issue #198 are tracked as
follow-ups:

- **Code-review bot** — firewall on diff / grep outputs, bounded routing
  over a fixed set of analysis tools, latency-sensitive answer phase.
- **Real-time voice agent** — Pipecat-backed, tight answer-phase budgets,
  `asyncio.to_thread(mgr.build_sync, …)` pattern.

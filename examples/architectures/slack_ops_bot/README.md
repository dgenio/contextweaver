# Slack ops bot — reference architecture

> An internal Slack bot fronting ~48 ops tools. Demonstrates how routing,
> the context firewall, and persistent facts compose end-to-end around a
> realistic multi-turn investigation.

## Run it

```bash
python examples/architectures/slack_ops_bot/main.py
```

(Or `make architectures` / `make example`.)

A captured run of the script lives in [`OUTPUT.md`](OUTPUT.md).

## What this is (and isn't)

This is a **reference architecture**, not a tutorial recipe. The cookbook
gives you copy-paste snippets for individual primitives (routing, firewall,
drilldown, BYO tools); the architecture wires them together around a
realistic problem shape so you can see how they interact.

It is **mocked**: tool implementations return canned strings, no real Slack
or backend systems are touched. The point is to demonstrate the
contextweaver glue, not to integrate with Slack.

## Setup

The 48-tool catalog lives in [`catalog.yaml`](catalog.yaml). Loading it:

```python
from contextweaver.routing.catalog import Catalog, load_catalog_yaml

catalog = Catalog()
for item in load_catalog_yaml("catalog.yaml"):
    catalog.register(item)
```

Namespaces: `logs`, `deploy`, `oncall`, `alerts`, `tickets`, `metrics`,
`identity`, `infra`, `feature`. Some tools have side effects
(`deploy.rollback`, `tickets.create`); the catalog records this on each
`SelectableItem.side_effects` so a real deployment could refuse to call
them automatically (`Router.route(..., exclude_tags=...)`).

## The investigation

The bot walks a six-turn transcript covering: who is on call, what the
logs say, what the current deploy state is, how to roll back, how to open
a ticket, and what tomorrow's schedule looks like. See `TRANSCRIPT` in
[`main.py`](main.py) for the exact text.

## What's load-bearing

| contextweaver feature | Used | What it does here |
|---|---|---|
| `Router.route(query)` | ✅ | Narrows 48 tools → top-3 shortlist (`top_k=3`) |
| Bounded choice pattern | ✅ | Bot picks from the shortlist, not from the whole catalog |
| `TreeBuilder` DAG | ✅ | One-shot graph build at startup; routes are sub-millisecond |
| Context firewall | ✅ | Compacts the 34 KB log dump to a 500-char summary before it touches the prompt |
| `ArtifactStore` | ✅ | Raw log bytes parked out-of-band for later drill-down if needed |
| `FactStore` | ✅ | On-call engineer + incident ticket survive across turns into the answer prompt |
| `Phase`-specific budgets | ✅ | Route phase budget (1.5K), answer-phase budget (4K) — tight enough that the budget visibly matters |
| Dependency closure | ✅ | Tool calls + tool results stay paired as `select_and_pack` evicts items |

## What's intentionally not used

| Not used | Why |
|---|---|
| Real Slack adapter | Out of scope — mocked transcript proves the architecture |
| LLM in the loop | The intent-map in `_select_from_shortlist` stands in for an LLM picking from the shortlist; swap it for any model call |
| Async build (`build()` vs. `build_sync()`) | Slack bot turn latency is forgiving; the voice-agent reference architecture (follow-up) will exercise `asyncio.to_thread(mgr.build_sync, …)` |
| Drilldown (`mgr.drilldown_sync`) | Not exercised here. See [Cookbook recipe 4](../../../docs/cookbook.md#4-firewall--drilldown-for-large-tool-outputs) |
| Sensitivity enforcement | The mocked transcript is all `Sensitivity.public`. A production bot would set `ContextPolicy.sensitivity_floor=Sensitivity.internal` and tag PII items appropriately |
| Alternative scorer backends (`bm25`, `fuzzy`) | The default TF-IDF scorer covers this catalog; for noisy real-world catalogs try `Router(scorer_backend='bm25')` |

## How to adapt it

1. Replace `_TOOL_RESPONSES` with calls to your real ops backends.
2. Replace `_select_from_shortlist` with an LLM call that picks from the
   shortlist (the shortlist is in `Router.route(...).candidate_ids`).
3. Tighten `ContextBudget` once you measure your prompt sizes.
4. Add a `RedactionHook` if any of your real tool outputs carry PII.
5. Persist `mgr.fact_store` to a durable backend if you want facts to
   survive process restarts (the current `InMemoryFactStore` is per-run).

## Follow-up architectures

Tracked under issue #198:

- Code-review bot — firewall on diff / grep outputs, latency-sensitive answer phase
- Real-time voice agent — Pipecat integration, tight async budgets

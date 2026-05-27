# Adopter Benchmark Report

> A decision-oriented companion to the generated
> [benchmark scorecard](https://github.com/dgenio/contextweaver/blob/main/benchmarks/scorecard.md).
> The scorecard is the source of truth for raw numbers; this page explains what
> those numbers mean for an engineering lead evaluating contextweaver.

## Executive Summary

contextweaver helps most when an existing agent already works, but its prompt is
being crowded by tool schemas, tool results, or long tool-use history.

| Best fit | Why contextweaver helps |
|---|---|
| MCP / FastMCP / Python agents with many tools | The Routing Engine shows the model a bounded `ChoiceCard` shortlist instead of the whole tool catalog. |
| Tools that return large JSON, logs, tables, CSV, or binary payloads | The context firewall stores raw output as artifacts and injects compact summaries. |
| Long multi-turn agents with tool calls/results | Phase-specific context builds keep dependency chains intact while dropping lower-value context first. |

| Poor fit | Why it may not help |
|---|---|
| A small agent with 3-5 tiny tools and short conversations | There may be no prompt-budget pressure to relieve. |
| A team looking for an agent framework or model SDK | contextweaver does not run the loop or call the LLM. |
| A pure RAG or long-term-memory problem | Use a retrieval or memory system first; contextweaver can budget their outputs later. |
| A very large catalog with sparse metadata and only lexical scoring | Default recall degrades at large catalog sizes; use a stronger retriever or improve catalog metadata. |

## Prompt-Size and Cost Model

The committed scorecard compares contextweaver against a naive baseline that
concatenates all tool schemas plus full conversation history. The token counts
below come from `benchmarks/results/latest.json` and are rendered in
`benchmarks/scorecard.md`.

Cost math is intentionally model-agnostic:

```text
cost_per_1k_turns = tokens_per_turn * 1000 * input_price_per_1m_tokens / 1_000_000
```

Use your own provider's input-token price. The worked example below uses an
illustrative `input_price_per_1m_tokens = $1.00`.

| Scenario | Naive tokens | contextweaver tokens | Reduction | Cost / 1k turns naive | Cost / 1k turns cw |
|---|---:|---:|---:|---:|---:|
| `large_catalog` | 2,767 | 1,514 | 45.28 % | $2.77 | $1.51 |
| `long_conversation` | 4,365 | 2,548 | 41.63 % | $4.37 | $2.55 |
| `mixed_payload` | 2,277 | 497 | 78.17 % | $2.28 | $0.50 |
| `short_conversation` | 1,946 | 496 | 74.51 % | $1.95 | $0.50 |
| `stress_conversation` | 17,482 | 6,651 | 61.96 % | $17.48 | $6.65 |
| `tiny_payload` | 1,704 | 267 | 84.33 % | $1.70 | $0.27 |

Average across the six committed scenarios: 5,090 naive tokens vs 1,996
contextweaver tokens per answer-phase build. At the illustrative price above,
that is about `$5.09` vs `$2.00` per 1,000 similarly-shaped turns.

**Important:** this is a prompt-input estimate, not an end-to-end production
cost guarantee. Completion tokens, model latency, retries, tool latency, cache
hits, and business logic all live outside this offline harness.

## Latency Model

Latency numbers in the scorecard are hardware-dependent. Treat them as ordering
and scale signals, not as service-level objectives.

| Catalog size | Queries | Recall@5 | MRR | p50 route latency | p95 route latency | p99 route latency |
|---:|---:|---:|---:|---:|---:|---:|
| 50 | 131 | 0.5649 | 0.4978 | 0.423 ms | 0.548 ms | 0.759 ms |
| 83 | 200 | 0.3825 | 0.3242 | 0.667 ms | 0.779 ms | 1.134 ms |
| 1000 | 200 | 0.1475 | 0.1456 | 36.007 ms | 37.467 ms | 41.711 ms |

The context-build side is measured as token output and budget behavior rather
than wall-clock latency in the public scorecard. For real deployments, measure
your own event-log shape with OpenTelemetry or a wrapper around
`ContextManager.build_sync(...)`.

## Routing-Quality Model

The default routing backends are deterministic and transparent. They are also
not magic: lexical scoring degrades as catalog size and noise increase.

| Observation | What it means |
|---|---|
| Recall@5 is 0.5649 at 50 tools and 0.1475 at 1000 tools in the headline run. | Routing-only adoption is strongest for small-to-medium catalogs or catalogs with strong metadata. |
| The scorecard includes `tfidf`, `bm25`, `embedding_hashing`, and skipped optional backends. | Backends are swappable; compare them on your catalog rather than assuming the default is best. |
| The mixed-shape 500-tool catalog reports lower recall than the uniform catalog. | Real catalogs with head-heavy namespaces and long tails need better metadata or a retriever-first shortlist. |

Recommended mitigation path for large catalogs:

1. Improve `SelectableItem.description`, `tags`, and `namespace` quality.
2. Compare `tfidf`, `bm25`, and `embedding_hashing` with
   `make benchmark-matrix`.
3. Add a retriever-first shortlist through the `Retriever` protocol when the
   catalog is too large or too semantically sparse for lexical ranking alone.

## Failure Modes Avoided

| Failure mode | What contextweaver does |
|---|---|
| Giant tool output pasted raw into the answer prompt | Stores the raw payload as an `ArtifactRef` and injects a summary. |
| Full schema catalog injected on every route turn | Exposes compact `ChoiceCard`s and hydrates full schema only when needed. |
| Manual history trimming drops a parent tool call | Dependency closure keeps selected tool results paired with their parent calls. |
| Confidential context crosses a policy boundary | Sensitivity filtering drops or redacts according to `ContextPolicy`. |
| Debugging prompt changes becomes guesswork | `BuildStats`, route traces, and explanations show what was included, dropped, and why. |

## Reproduce

```bash
make benchmark-matrix
make scorecard
make scorecard-check
```

Source files:

| File | Role |
|---|---|
| [`benchmarks/results/latest.json`](https://github.com/dgenio/contextweaver/blob/main/benchmarks/results/latest.json) | Machine-readable benchmark output. |
| [`benchmarks/scorecard.md`](https://github.com/dgenio/contextweaver/blob/main/benchmarks/scorecard.md) | Generated scorecard and methodology. |
| [`benchmarks/benchmark.py`](https://github.com/dgenio/contextweaver/blob/main/benchmarks/benchmark.py) | Harness implementation. |
| [`benchmarks/scenarios/`](https://github.com/dgenio/contextweaver/tree/main/benchmarks/scenarios) | Context-pipeline fixtures. |
| [`benchmarks/routing_gold.json`](https://github.com/dgenio/contextweaver/blob/main/benchmarks/routing_gold.json) | Routing gold set. |

Deterministic metrics: recall, drops, dedup counts, token counts, artifact
counts, and compaction ratios.

Hardware-dependent metrics: p50/p95/p99 latency.

## How to Decide

Adopt a small proof-of-concept if at least one of these is true:

- Your prompt regularly includes more tools than the model should reason about.
- A single tool can return enough data to dominate the prompt.
- Your agent history includes tool-call chains that manual truncation breaks.
- You need deterministic context budgeting and inspectable drop reasons.

Defer contextweaver if none of those are true yet. Keep the scorecard commands
around, then re-run them once the agent grows into the problem.

# Benchmarks

> contextweaver ships a public, reproducible benchmark scorecard so you can
> see top-k recall, token savings, and routing latency before integrating.

## TL;DR

| What | Where |
|---|---|
| The numbers | [`benchmarks/scorecard.md`](https://github.com/dgenio/contextweaver/blob/main/benchmarks/scorecard.md) — committed, regeneratable in one command |
| The raw output | [`benchmarks/results/latest.json`](https://github.com/dgenio/contextweaver/blob/main/benchmarks/results/latest.json) — machine-readable |
| The harness | [`benchmarks/benchmark.py`](https://github.com/dgenio/contextweaver/blob/main/benchmarks/benchmark.py) |
| The methodology | [`benchmarks/README.md`](https://github.com/dgenio/contextweaver/blob/main/benchmarks/README.md) |

Reproduce locally:

```bash
make benchmark   # writes benchmarks/results/latest.json
make scorecard   # writes benchmarks/scorecard.md from latest.json
git diff --quiet benchmarks/scorecard.md   # passes on clean re-run with same seed
```

The check `git diff --quiet benchmarks/scorecard.md` is the determinism gate:
identical inputs must produce byte-identical scorecard output.

## What is measured

**Routing.** Precision\@k, recall\@k, MRR, and p50/p95/p99 latency at catalog
sizes 50 / 83 / 1000 against a fixed gold dataset of 200 hand-curated queries
covering all 8 catalog namespaces (`benchmarks/routing_gold.json`). The
1000-item catalog is the natural 83-item pool extended with synthetic
variants so beam search has to work harder; the gold queries only match the
non-synthetic items, so accuracy numbers stay valid as catalog size scales.

**Context pipeline.** For each scenario in `benchmarks/scenarios/`:

- `event_count`, `items_included`, `items_dropped` (budget pressure),
  `dedup_removed` (near-duplicate removal)
- `prompt_tokens` and `budget_utilization_pct` against the answer-phase
  budget (default `6000` tokens)
- `artifacts_created` (firewall interceptions) and `avg_compaction_ratio`
  (raw artifact size ÷ injected summary size)

The current scenarios:

| Scenario | Turns | Purpose |
|---|---:|---|
| `short_conversation.jsonl` | 5 user turns | Baseline; low pressure |
| `long_conversation.jsonl` | 20 user turns | Mid pressure with one large invoice search result |
| `large_catalog.jsonl` | 15 user turns | Routing breadth across all 8 namespaces |
| `stress_conversation.jsonl` | 50+ user turns | Stresses budget-driven drop + dedup + multi-result compaction (#181) |

## How to read these numbers

The scorecard reports four distinct kinds of measurement; each one means
something different and degrades under different conditions:

| Metric | What it measures | What's stable | What isn't |
|---|---|---|---|
| `recall@k`, `precision@k`, `MRR` | Whether the **default lexical scorer** (`tfidf` baseline, `bm25` optional) puts the gold-set tool inside the top-`k` shortlist | Reproducible across machines for a given seed and gold set | A **floor**, not a ceiling — the scorer is pluggable via `Router(scorer_backend=...)`; embedding-based and LLM-rerank backends are out of scope for this baseline scorecard |
| `p50` / `p95` / `p99` latency (ms) | Wall-clock cost of one `Router.route(query)` call | **Relative ordering** (50 < 83 < 1000 always) | **Absolute microseconds** vary with hardware, Python build, and other load on the runner; the docs explicitly call this out |
| `prompt_tokens`, `budget_utilization_pct` | Output of the Context Engine for a fixed scenario log | Reproducible across machines (`CharDivFourEstimator`, no `tiktoken` state dependency) | The scenarios are illustrative — your event log will produce different numbers |
| `avg_compaction_ratio`, `artifacts_created` | Firewall behaviour: how much raw tool output is intercepted | Reproducible | Driven by the **scenarios' tool outputs**, not by a property of contextweaver — if your real tool calls return tiny payloads, compaction is `1.0×` |

Two practical reading rules:

1. **Treat `recall@5` at catalog_size 1000 as a baseline number, not a ceiling.** The default `tfidf` and `bm25` scorers are lexical; they trade recall for transparency and zero-network determinism. A retriever-first shortlist (embedding ANN or LLM rerank) is the documented way to recover recall at scale — tracked under issue [#8](https://github.com/dgenio/contextweaver/issues/8).
2. **Token-reduction percentages are scenario-bound.** "41.6%–74.5%" is the *range across the four committed scenarios*, not a guarantee for any specific workload. The right number for your agent is whatever you measure on your own event logs using `scripts/baseline_naive.py`.

## Known limits and honest framing

The numbers above describe what the **default** contextweaver
configuration does on the **default** benchmark fixtures. The
following are deliberate, documented gaps — not bugs:

- **Routing recall degrades with catalog size.** At catalog_size 50, `recall@5` is ~0.56. At 1000, it falls to ~0.15 with both `tfidf` and `bm25`. This is the well-known lexical-retrieval ceiling on large tool catalogs. contextweaver's response is to make the scorer **pluggable** (see `Router(scorer_backend=...)` and the `Retriever` protocol on the `EngineRegistry`), not to claim the default scorer is sufficient at scale. The scorecard exists so you can see this drop yourself, swap in a stronger backend, and re-measure.
- **Token reduction is scenario-driven.** All percentages above are measured against the four committed scenarios under `benchmarks/scenarios/`. If your real conversations or tool outputs differ materially in shape (much shorter turns, much larger results, very different namespace distribution), expect different percentages.
- **Latency is informational.** Treat the latency numbers as ordering between catalog sizes, not as a service-level objective for production deployments.
- **No claim is made** about end-to-end agent cost, answer quality, or accuracy — those depend on the model and the agent loop, not on contextweaver alone.

If you find a regime where the default backends underperform, file an issue with your gold set and catalog — the harness is designed to absorb new scenarios.

## Gateway scenarios (range)

The [MCP Context Gateway reference architecture](architectures/mcp_context_gateway.md) (`examples/architectures/mcp_context_gateway/`) ships a single-turn reference run that exercises the launch narrative end-to-end. As of issue #270 the harness also runs **five** deterministic gateway scenarios over the same 60-tool catalog so the reduction number is reported as a measured range, not a single anecdote:

| Scenario | Raw chars | Summary chars | Firewall reduction | Artifact |
|---|---:|---:|---:|:-:|
| `tiny_ack` | 34 | 34 | 0.0 % | — |
| `small_post` | 77 | 77 | 0.0 % | — |
| `medium_ticket` | 238 | 238 | 0.0 % | — |
| `large_log` | 14,206 | 501 | 96.5 % | ✓ |
| `bigquery_rowset` | 16,507 | 194 | 98.8 % | ✓ |

**Headline range:** firewall reduction across five gateway scenarios runs **0.0 % – 98.8 %**. The 0.0 % cases are not regressions — they're the firewall correctly no-op'ing on payloads under the `firewall_threshold=2000` threshold. The full per-scenario detail (routing query, chosen tool, exposed cards, answer-phase tokens) is committed at [`benchmarks/gateway_scorecard.md`](https://github.com/dgenio/contextweaver/blob/main/benchmarks/gateway_scorecard.md).

The single-turn architecture run still produces the same metrics (`bigquery_rowset` row above, plus `hydrated_schema_chars = 854` and `final_prompt_tokens = 142` from the answer-phase build); see [`examples/architectures/mcp_context_gateway/OUTPUT.md`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/mcp_context_gateway/OUTPUT.md) for the captured run.

> **Reading this honestly:** the 98.8 % high-water mark only applies to the `bigquery_rowset` scenario. A real gateway deployment's per-call reduction will vary with the shape of each upstream response; the explicit floor at 0.0 % is the load-bearing claim that the firewall does not penalise small responses.

Reproduce byte-for-byte via:

```bash
python examples/architectures/mcp_context_gateway/main.py   # single-turn run
make benchmark-gateway && make gateway-scorecard            # 5-scenario range
make gateway-scorecard-check                                # CI: byte-stable
```

## Why this exists

Pre-scorecard, the README compared "70% lower cost" and "sub-second latency"
as illustrative arithmetic — useful as intuition, but not tied to any
specific catalog, dataset, or configuration. The scorecard fills that gap:
public numbers that anyone can regenerate from a clean checkout, that CI
re-runs as an informational step on every PR, and that downstream library
authors can use as a reference for what "good" looks like.

## Scope notes

This first cut of the scorecard ships:

- the default scorer backend (`tfidf`)
- catalog sizes 50 / 83 / 1000 as defined by `benchmarks/benchmark.py`
- four scenarios under `benchmarks/scenarios/`

Tracked as follow-ups (see issue #197):

- Per-backend matrix comparing `tfidf` vs `bm25` vs `fuzzy`
- 100 / 500 / 1000 catalog sizes (rename + scenario tune to match)
- Weekly scheduled CI run that regenerates the scorecard and opens a drift PR
- Hardware reference table — pin a specific runner spec for absolute-latency
  numbers

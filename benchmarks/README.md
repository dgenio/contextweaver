# contextweaver Benchmark Harness

Measures **routing accuracy** and **context pipeline efficiency** using proxy
metrics — no LLM calls, no external network access.  Results are deterministic
for a given seed.

## Quick start

```bash
# From the repo root (virtualenv active):
make benchmark

# Or directly:
python benchmarks/benchmark.py
python benchmarks/benchmark.py --k 10 --output benchmarks/results/latest.json
```

## What is measured

### Routing metrics

Evaluated against `routing_gold.json` — a hand-crafted set of 50 queries with
expected tool IDs drawn from all 8 catalog namespaces.

| Metric | Description |
|--------|-------------|
| `precision@k` | Fraction of top-k results that are in the expected set |
| `recall@k` | Fraction of expected items found in top-k |
| `mrr` | Mean Reciprocal Rank of first correct result |
| `p50_ms` / `p95_ms` / `p99_ms` | Routing latency percentiles (milliseconds) |

Benchmarked at three catalog sizes:

- **50** — draw from `generate_sample_catalog(50)`
- **83** — full natural pool of `generate_sample_catalog()`
- **1000** — 83-item base extended with synthetic variants; tests latency at scale

> Precision@k is naturally lower for single-expected-item queries (max = 1/k).
> Recall@k is the primary accuracy signal.  MRR measures ranking quality.

### Context pipeline metrics

Measured by loading each scenario JSONL, pushing events into `InMemoryEventLog`,
and calling `ContextManager.build_sync(phase=Phase.answer)`.

| Metric | Description |
|--------|-------------|
| `event_count` | Total items in the scenario |
| `items_included` | Items selected into the compiled prompt |
| `items_dropped` | Items excluded (budget, sensitivity, TTL) |
| `dedup_removed` | Near-duplicate items removed by dedup stage |
| `prompt_tokens` | Estimated token count of compiled prompt (`len // 4`) |
| `budget_utilization_pct` | `prompt_tokens / budget.answer * 100` |
| `artifacts_created` | Firewall-intercepted tool results stored as artifacts |
| `avg_compaction_ratio` | Mean `raw_bytes / summary_bytes` across intercepted results |

## Scenarios

| File | Turns | Tools | Notes |
|------|-------|-------|-------|
| `short_conversation.jsonl` | 5 | 4 (notifications.send, email.draft, email.send, deployments.status) | Infra freeze-prep workflow |
| `long_conversation.jsonl` | 20 | 10+ | Complex CRM/billing/infra workflow; includes large invoice search result |
| `large_catalog.jsonl` | 15 | 10+ (all 8 namespaces) | Research catalog workflow; exercises routing breadth |
| `stress_conversation.jsonl` | 30+ | 25+ | SEV2 incident-response transcript with 3 large tool results (firewall fires), 4 near-duplicate pairs (dedup fires), and total token volume that pushes the 6000-token answer budget into drop territory (#181). Hand-authored to hit specific firewall/dedup/drop targets; if it needs to be regenerated, see the `#181` acceptance criteria for the invariants the new fixture must satisfy. |

## Baseline metrics (seed=42, k=5)

Run on 2026-05-15 with contextweaver v0.3.0+, Python 3.11. Latency numbers
are environment-dependent; recall, drops, dedup, and token counts are not.
The committed [`scorecard.md`](../benchmarks/scorecard.md) is rendered from
the same `results/latest.json`.

### Routing

| catalog_size | queries | prec@5 | recall@5 |   mrr | p50_ms | p95_ms | p99_ms |
|-------------:|--------:|-------:|---------:|------:|-------:|-------:|-------:|
|           50 |      50 | 0.1600 |   0.7400 | 0.7100 |  0.296 |  0.430 |  0.538 |
|           83 |      50 | 0.1320 |   0.6100 | 0.6083 |  0.473 |  0.557 |  0.859 |
|         1000 |      50 | 0.0640 |   0.3100 | 0.3200 | 28.540 | 30.422 | 34.245 |

Notes:
- All 50 gold queries evaluated at every catalog size (catalog generated with matching `n` so all gold IDs are present)
- Recall degrades predictably as catalog grows (noise items compete with true matches)
- p50/p95 stay under a millisecond up to catalog_size 83; at catalog_size 1000 the p99 climbs into the tens of milliseconds because beam search has to evaluate substantially more children — that's the regime where the `Retriever`/`EngineRegistry` shortlist is the next step

### Context pipeline

| scenario             | events | incl | drop | dedup |  tok |  util% | arts | compact |
|:---------------------|-------:|-----:|-----:|------:|-----:|-------:|-----:|--------:|
| large_catalog        |     60 |   60 |    0 |     0 | 1514 |  25.2% |   15 |  1.00x  |
| long_conversation    |     82 |   82 |    0 |     0 | 2548 |  42.5% |   21 |  1.41x  |
| short_conversation   |     18 |   18 |    0 |     0 |  496 |   8.3% |    4 |  1.00x  |
| stress_conversation  |    147 |  136 |    7 |     4 | 6651 | 110.9% |   32 |  3.29x  |

Notes:
- `compact=1.00x` means tool results are small enough that the firewall summary ≈ raw length
- `long_conversation` reaches 1.41x compaction from the large invoice search response
- `stress_conversation` is the new budget-pressure scenario (#181): three large tool results (logs, grep, dashboard) push average compaction to 3.29x, four near-duplicate agent messages drive `dedup_removed=4`, and the total prompt size pushes past the 6000-token answer budget so `items_dropped=7`
- The other three scenarios remain a "light load" baseline so reviewers can see the difference between unloaded and stressed pipeline behaviour

## Output

Results are written to `benchmarks/results/latest.json` and committed so
that the [`scorecard.md`](../benchmarks/scorecard.md) (also committed)
stays in sync with the source numbers. `make scorecard` regenerates the
scorecard from `latest.json`; CI runs `make scorecard-check` and fails on
drift.

To compare across runs, copy `latest.json` to a dated filename before re-running:

```bash
cp benchmarks/results/latest.json benchmarks/results/$(date +%Y%m%d).json
```

## Adding queries to the gold dataset

Edit `benchmarks/routing_gold.json`.  Each entry:

```json
{"query": "natural language query", "expected": ["tool.id.one", "tool.id.two"], "tags": ["namespace"]}
```

- `expected`: IDs from `examples/sample_catalog.json`.  At least one must be present in
  every catalog size you want to evaluate against.
- `tags`: informational only; used for future filtering.

## CI integration

The benchmark step runs with `continue-on-error: true` in `.github/workflows/ci.yml`.
It is **informational only** — failures do not block merges.  This is intentional:
baseline drift is expected and should be reviewed manually, not treated as a hard error.

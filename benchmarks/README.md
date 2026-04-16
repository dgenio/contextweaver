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

## Baseline metrics (seed=42, k=5)

Run on 2026-04-15 with contextweaver v0.1.7, Python 3.12.

### Routing

| catalog_size | queries | prec@5 | recall@5 |   mrr | p50_ms | p95_ms | p99_ms |
|-------------:|--------:|-------:|---------:|------:|-------:|-------:|-------:|
|           50 |      50 | 0.1600 |   0.7400 | 0.7100 |  0.124 |  0.272 |  0.452 |
|           83 |      50 | 0.1320 |   0.6100 | 0.6083 |  0.143 |  0.317 |  0.752 |
|         1000 |      50 | 0.0640 |   0.3100 | 0.3200 |  0.353 |  0.742 |  1.057 |

Notes:
- All 50 gold queries evaluated at every catalog size (catalog generated with matching `n` so all gold IDs are present)
- Recall degrades predictably as catalog grows (noise items compete with true matches)
- p99 latency stays under 1.1ms even at 1000 items

### Context pipeline

| scenario             | events | incl | drop | dedup |  tok | util% | arts | compact |
|:---------------------|-------:|-----:|-----:|------:|-----:|------:|-----:|--------:|
| large_catalog        |     60 |   60 |    0 |     0 | 1514 | 25.2% |   15 |  1.00x  |
| long_conversation    |     82 |   82 |    0 |     0 | 2548 | 42.5% |   21 |  1.41x  |
| short_conversation   |     18 |   18 |    0 |     0 |  496 |  8.3% |    4 |  1.00x  |

Notes:
- `compact=1.00x` means tool results are small enough that the firewall summary ≈ raw length
- `long_conversation` reaches 1.41x compaction from the large invoice search response
- 0% budget pressure at 6000-token answer budget; increase event density or reduce budget to stress-test

## Output

Results are written to `benchmarks/results/latest.json`.  This path is
git-ignored; only the `.gitkeep` placeholder is tracked.

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

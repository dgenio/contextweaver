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

Run on 2026-05-16 with contextweaver v0.4.0+, Python 3.11. Latency numbers
are environment-dependent; recall, drops, dedup, and token counts are not.
The committed [`scorecard.md`](../benchmarks/scorecard.md) is rendered from
the same `results/latest.json`. As of #208/#209 the harness emits a
**per-backend × per-size matrix** (`tfidf` / `bm25` / `fuzzy` × 100 / 500
/ 1000) plus **per-namespace recall@5** under `routing.matrix` and
`routing.per_namespace`. The legacy single-backend rows (50 / 83 / 1000
on `tfidf`) are preserved verbatim for back-compat.

### Routing (legacy single-backend rows)

| catalog_size | queries | prec@5 | recall@5 |   mrr | p50_ms | p95_ms | p99_ms |
|-------------:|--------:|-------:|---------:|------:|-------:|-------:|-------:|
|           50 |     115 | 0.1165 |   0.5652 | 0.4919 |  0.304 |  0.390 |  0.443 |
|           83 |     200 | 0.0780 |   0.3825 | 0.3253 |  0.476 |  0.565 |  0.769 |
|         1000 |     200 | 0.0300 |   0.1475 | 0.1325 | 25.332 | 29.268 | 36.844 |

Notes:
- Gold set expanded from 50 → 200 hand-authored queries (#209). Queries
  whose `expected` tools don't exist at the smaller catalog size (50) are
  skipped, hence `queries: 115` rather than 200 in the first row.
- Recall numbers are lower than the v0.3.0 baseline (0.74 / 0.61 / 0.31)
  because the expanded gold set is markedly harder — it includes
  naturalistic queries on every tool, not just the headline operations.
- See `matrix` and `per_namespace` blocks in `latest.json` (#208 / #209)
  for the new published surface.

### Routing matrix (run `make benchmark-matrix`)

The 3 × 3 cross-product. ⚠️ markers in the scorecard flag cells whose
p99 exceeds `min_p99 × 1.30` at the same catalog size.

| backend | catalog_size | queries | recall@5 |   mrr | p99_ms |
|:--------|-------------:|--------:|---------:|------:|-------:|
| tfidf   |          100 |     200 |   0.3825 | 0.3229 |  0.999 |
| tfidf   |          500 |     200 |   0.2675 | 0.2413 |  8.200 |
| tfidf   |         1000 |     200 |   0.1475 | 0.1325 | 28.642 |
| bm25    |          100 |     200 |   0.3825 | 0.3232 |  4.653 |
| bm25    |          500 |     200 |   0.2750 | 0.2491 | 24.536 |
| bm25    |         1000 |     200 |   0.1475 | 0.1392 | 80.929 |
| fuzzy   |          100 |     200 |   0.4675 | 0.3638 |  0.704 |
| fuzzy   |          500 |     200 |   0.2350 | 0.2232 |  9.225 |
| fuzzy   |         1000 |     200 |   0.1500 | 0.1473 | 30.527 |

### Context pipeline

| scenario             | events | incl | drop | dedup |  tok |  util% | arts | compact | naive→cw red. |
|:---------------------|-------:|-----:|-----:|------:|-----:|-------:|-----:|--------:|--------------:|
| large_catalog        |     60 |   60 |    0 |     0 | 1514 |  25.2% |   15 |  1.00x  |          0.0% |
| long_conversation    |     82 |   82 |    0 |     0 | 2548 |  42.5% |   21 |  1.41x  |         10.2% |
| short_conversation   |     18 |   18 |    0 |     0 |  496 |   8.3% |    4 |  1.00x  |          0.0% |
| stress_conversation  |    147 |  136 |    7 |     4 | 6651 | 110.9% |   32 |  3.29x  |         58.3% |

Notes:
- `compact=1.00x` means tool results are small enough that the firewall summary ≈ raw length.
- `long_conversation` reaches 1.41x compaction from the large invoice search response.
- `stress_conversation` is the budget-pressure scenario (#181): three large tool results (logs, grep, dashboard) push average compaction to 3.29x, four near-duplicate agent messages drive `dedup_removed=4`, and the total prompt size pushes past the 6000-token answer budget so `items_dropped=7`.
- `naive→cw red.` is the percent reduction vs the naïve "concatenate every event's text" baseline (#215), computed with the same `CharDivFourEstimator` so numerator and denominator are directly comparable. The light-load scenarios show 0% because their input is already under contextweaver's render overhead; the saving is in the stress regime.

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

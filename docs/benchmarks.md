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

**Routing.** Precision\@k, recall\@k, MRR, and p50/p95/p99 latency against a
fixed gold dataset of 200 hand-curated queries with ≥20 queries per namespace
(`benchmarks/routing_gold.json`, #209). Two surfaces:

- **Legacy single-backend rows** — catalog sizes 50 / 83 / 1000 on TF-IDF,
  preserved for back-compat with pre-#208 consumers.
- **Per-backend × per-size matrix** — `tfidf` / `bm25` / `fuzzy` at catalog
  sizes 100 / 500 / 1000 (run with `make benchmark-matrix`, #208). Missing
  optional backends (`fuzzy` without `[retrieval]`) are recorded with a
  ``status`` field, never silently omitted.

**Per-namespace recall.** Breakdown of recall@k by namespace at the largest
matrix size (#209). Lets reviewers see which namespaces are noisier on each
backend without re-running the harness.

**Context pipeline.** For each scenario in `benchmarks/scenarios/`:

- `event_count`, `items_included`, `items_dropped` (budget pressure),
  `dedup_removed` (near-duplicate removal)
- `prompt_tokens` and `budget_utilization_pct` against the answer-phase
  budget (default `6000` tokens)
- `artifacts_created` (firewall interceptions) and `avg_compaction_ratio`
  (raw artifact size ÷ injected summary size)
- `naive_delta` (#215) — naïve concat token count, contextweaver token
  count, percent reduction, and a parent-chain coverage proxy. Same
  estimator (``CharDivFourEstimator``) on both sides so the ratio is
  hardware-independent.

The current scenarios:

| Scenario | Turns | Purpose |
|---|---:|---|
| `short_conversation.jsonl` | 5 user turns | Baseline; low pressure |
| `long_conversation.jsonl` | 20 user turns | Mid pressure with one large invoice search result |
| `large_catalog.jsonl` | 15 user turns | Routing breadth across all 8 namespaces |
| `stress_conversation.jsonl` | 50+ user turns | Stresses budget-driven drop + dedup + multi-result compaction (#181) |

## Latency budget markers

The scorecard and the PR delta comment both flag a matrix or cell with
``⚠️`` when its p99 exceeds ``baseline × 1.30``; ``✅`` otherwise. In the
scorecard the baseline is the fastest backend at the same catalog size;
in the PR delta it's the corresponding cell on ``main``. The +30%
threshold is the documented convention (Round 2 Q5=C).

## CI integration

- **Per-PR delta** — `.github/workflows/benchmark-delta.yml` runs the
  matrix on every PR, diffs it against the committed `main` baseline,
  and posts a sticky comment (one per PR, updated in place) with
  recall@k and latency deltas (#211). Soft: ``continue-on-error: true``;
  the workflow never blocks merges.
- **Weekly scheduled regen** — `.github/workflows/scorecard-weekly.yml`
  runs Mondays at 06:00 UTC and opens a drift PR via
  ``peter-evans/create-pull-request`` (#207).
- **Scorecard drift check** — `make scorecard-check` gates every PR
  through the main `ci.yml` workflow.

## Why this exists

Pre-scorecard, the README compared "70% lower cost" and "sub-second latency"
as illustrative arithmetic — useful as intuition, but not tied to any
specific catalog, dataset, or configuration. The scorecard fills that gap:
public numbers that anyone can regenerate from a clean checkout, that CI
re-runs as an informational step on every PR, and that downstream library
authors can use as a reference for what "good" looks like.

## ScoringConfig sweep (companion report)

`make sweep-scoring` runs a 243-point grid search over the five
``ScoringConfig`` weights and writes
[`benchmarks/sweep_scoring.md`](https://github.com/dgenio/contextweaver/blob/main/benchmarks/sweep_scoring.md)
ranking every configuration by a documented composite (50% coverage +
30% util-overrun penalty + 20% drop-rate penalty). Per #214 the report
**measures** alternatives; it does **not** change defaults — that's a
separate, deliberately-scoped follow-up.

## Scope notes

Tracked as follow-ups:

- Hardware reference table — pin a specific runner spec for absolute-latency
  numbers.
- Defaults change PR if `sweep_scoring.md` surfaces a Pareto-dominating
  configuration (separate from #214 per its non-goals).

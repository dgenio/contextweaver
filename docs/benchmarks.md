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
sizes 50 / 83 / 1000 against a fixed gold dataset of 50 hand-curated queries
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

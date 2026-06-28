# Scaling benchmark matrix

How contextweaver's routing behaves as the tool catalog grows — the
methodology, the reproducible commands, and how to read the numbers (issue
#687). This page ties together three deterministic, offline benchmarks that
each measure a different slice of "does this still work at scale?"

| Benchmark | Question it answers | Command | Output |
|---|---|---|---|
| Routing-scale profile | How does build/route **latency** scale to 10k tools, and how much does the persistent cache save? | `make benchmark-routing-scale` | [`routing-scale.md`](routing-scale.md) · `benchmarks/results/routing_scale.json` |
| Large-catalog quality | At 300+ tools across many namespaces, does routing keep the right tool **reachable**, enforce filters, and firewall large results? | `make benchmark-large-catalog` | `benchmarks/large_catalog_scorecard.md` · `benchmarks/results/large_catalog.json` |
| Per-backend matrix | How do `tfidf` / `bm25` / embedding backends compare across catalog sizes? | `make benchmark-matrix` | `benchmarks/scorecard.md` (matrix section) |

## Methodology

- **Deterministic and offline.** Catalogs are generated from a seeded pool
  (`generate_sample_catalog`) and extended with near-duplicate variants for
  larger sizes. No network and no model calls; token counts use the
  `CharDivFourEstimator` so accuracy and token figures are
  environment-independent.
- **Latency is host-dependent.** Treat latency columns as *ordering*, not
  absolutes — the relative cost between catalog sizes is portable, the
  absolute millisecond count is not. Quality metrics (recall@k, MRR, token
  reduction) are environment-independent and should be byte-identical on a
  clean re-run.
- **Scale points.** The routing-scale profile sweeps `100 → 1000 → 5000 →
  10000` tools. The large-catalog quality benchmark runs at 320 tools across
  8 namespaces with ~240 near-duplicate distractor tools and ~30 destructive
  (side-effecting) tools. It also routes a large synthetic invoice result through
  the context firewall and verifies the raw artifact remains recoverable through
  the artifact-view path.

## Reproducing the full matrix

```bash
make benchmark-routing-scale   # latency + cache speedup up to 10k tools
make benchmark-large-catalog   # recall/filter/firewall + prompt reduction at 300+ tools
make benchmark-matrix          # per-backend × per-size accuracy matrix
```

Each command writes a committed Markdown scorecard plus a machine-readable
JSON artifact under `benchmarks/results/`.

## Interpreting the results

- **Cold start dominates at scale.** In the routing-scale profile, graph
  construction (`TreeBuilder.build`) grows super-linearly and dominates cold
  start. Deployments that recreate a router per request over the same catalog
  should persist the graph and fitted index (`save_graph`/`load_graph` +
  `RoutingIndexCache`); the `cold speedup` column quantifies the win.
- **Recall degrades predictably with catalog size.** As distractors multiply,
  near-duplicate tools compete with the true match. The large-catalog
  scorecard reports recall@1/3/5 against this pressure; a drop below the
  scorecard's threshold floor is flagged as a regression.
- **Token reduction is the headline benefit.** Bounded `ChoiceCard`s shrink
  the routing prompt by ~95–97% versus listing every tool's name + description
  (the naive baseline these benchmarks measure; full JSON schemas would make
  the gap larger still) — and the gap widens as the catalog grows, which is
  exactly when naive all-tools prompting becomes untenable.

## Trend over releases

Per-release snapshots of the deterministic metrics are captured under
`benchmarks/results/history/` and rendered to
[`benchmarks/trend.md`](https://github.com/dgenio/contextweaver/blob/main/benchmarks/trend.md)
(`make trend`), so scaling regressions that creep in across releases stay
visible.

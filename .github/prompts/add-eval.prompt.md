---
mode: agent
description: Step-by-step workflow for adding an evaluation for a feature in contextweaver.
---

# Add an Eval

Follow these steps in order. Each step has a clear success criterion.

## References
- `AGENTS.md` — module map, hard rules, strong patterns, coding style
- `benchmarks/README.md` — harness reference + baseline tables
- `benchmarks/scorecard.md` — committed public scorecard
- `scripts/render_scorecard.py` — deterministic scorecard renderer
- `scripts/benchmark_delta.py` — sticky PR-comment delta generator (#211)
- `docs/agent-context/workflows.md` — canonical command reference

## When to use this prompt

Adding any of: new routing scorer, tokeniser change, scoring weight change,
context-pipeline stage, sensitivity rule, adapter that owns the
`ResultEnvelope.summary` writer, or anything else that can move
`recall@k`, `dropped`, `dedup_removed`, or `prompt_tokens` in the
benchmark output.

## Steps

1. **Identify the measurement surface.** Decide which substrate the feature
   moves: gold-set queries (`benchmarks/routing_gold.json`), scenario events
   (`benchmarks/scenarios/*.jsonl`), or the per-backend matrix dimensions
   (`benchmarks/benchmark.py:_DEFAULT_MATRIX_BACKENDS`/`_SIZES`).
   _Success: surface is identified._

2. **Extend the right input.** Add gold queries / scenario events / matrix
   cells. For gold queries: edit `benchmarks/routing_gold.json`; ensure
   every `expected` id exists in `examples/sample_catalog.json`; add a
   `namespace` field. For scenarios: drop a JSONL file in
   `benchmarks/scenarios/`; the benchmark picks it up automatically.
   _Success: input loads without error (`python benchmarks/benchmark.py
   --output /tmp/test.json` exits 0)._

3. **Regenerate the matrix.** `make benchmark-matrix` writes both the
   single-backend `routing` summary and the per-backend × per-size
   `routing_matrix` + `routing_per_namespace` keys to
   `benchmarks/results/latest.json`.
   _Success: `latest.json` contains the new cells._

4. **Regenerate the scorecard.** `make scorecard` writes
   `benchmarks/scorecard.md` from `latest.json` deterministically.
   _Success: `git diff --quiet benchmarks/scorecard.md` passes after two
   consecutive runs on the same seed (latency lines may differ run-to-run
   on the same hardware; recall / drops / dedup must be byte-identical)._

5. **Interpret the regression-comment delta.** Push the branch; the CI step
   `benchmark-comment` (in `.github/workflows/ci.yml`) posts a sticky PR
   comment with the delta vs `main`. Cells flagged with ⚠️ indicate either
   an accuracy regression > 1pp or a latency increase > base × 1.30.
   _Success: every ⚠️ cell has a written justification in the PR body
   "Reproducibility" section (`.github/pull_request_template.md`)._

6. **Confirm scorecard determinism.** Re-run `make benchmark-matrix && make
   scorecard` locally; confirm `git diff benchmarks/scorecard.md` shows
   only latency-line differences (or no diff at all).
   _Success: non-latency keys in `latest.json` are byte-identical to the
   committed file._

7. **Run `make ci`.** All declared targets
   (`fmt lint type test schemas-check example demo`)
   plus the `scorecard-check` CI step must pass.
   _Success: `make ci` exits 0; `python scripts/render_scorecard.py
   --check` exits 0._

## Reading the latency-budget markers

Both `scripts/render_scorecard.py` and `scripts/benchmark_delta.py` share
the same convention (Round 2 Q5=C):

- ✅ when `head_cell <= base_cell × 1.30`
- ⚠️ when `head_cell >  base_cell × 1.30`

For accuracy cells (`recall@k`, `MRR`):

- ✅ when `head_cell >= base_cell - 1pp`
- ⚠️ when `head_cell <  base_cell - 1pp`

The ⚠️ marker is informational — CI never blocks the PR on it.

## Anti-patterns to avoid

- Adding eval code to `src/contextweaver/` (per #215 design constraint: eval
  tooling lives in `scripts/` or `benchmarks/`, not the library).
- Inventing a new make target without registering it in
  `docs/agent-context/workflows.md` (per `lessons-learned.md` #5).
- Cherry-picking gold queries that show favourable results — the gold set
  is curated for honesty, not headline numbers.
- Hard-gating CI on a regression bound until the gold set is large enough
  that 1-query noise is below 0.5pp (currently 200 queries → noise floor is
  ~0.5pp).

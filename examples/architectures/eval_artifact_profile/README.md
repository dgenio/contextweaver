# Agent-safe evaluation-artifact context profile — reference architecture

> Offline policy-evaluation reports are large and easy to misread. An agent
> that sees only a headline score such as `V_hat` can draw a confident,
> wrong conclusion. This profile compiles an evaluation artifact into
> **phase-aware, agent-safe** context that foregrounds support health,
> uncertainty, and caveats — and only then the headline estimate.

## Run it

```bash
python examples/architectures/eval_artifact_profile/main.py
```

(Or `make architectures` / `make example`.)

A captured run of the script lives in [`OUTPUT.md`](OUTPUT.md).

## What this is (and isn't)

This is a **context-shaping profile**, not a statistics library. It does no
estimation — that belongs to the artifact producer (for example
[`skdr-eval`](https://github.com/dgenio/skdr-eval)). It takes a decoded
evaluation artifact and decides *what to show an agent, in what order, at
each phase*, so the agent never reasons from a bare estimate.

The profile is the function `compile_eval_context(artifact, phase)` in
[`main.py`](main.py). Three fixtures under [`fixtures/`](fixtures/) exercise
it at every risk level:

| Fixture | Decision status | Support |
|---|---|---|
| [`artifact_ok.json`](fixtures/artifact_ok.json) | `ok` | healthy (n≈18k, good overlap) |
| [`artifact_caution.json`](fixtures/artifact_caution.json) | `caution` | moderate (delta CI crosses zero) |
| [`artifact_high_risk.json`](fixtures/artifact_high_risk.json) | `high_risk` | poor (n=184, heavy extrapolation) |

## Behaviour by phase

- **Route** — summary metadata only: artifact type, available diagnostics,
  whether it needs interpretation or policy gating. No metrics, no estimate.
- **Interpret** — the full diagnostic view, ordered: support health →
  warnings → (limitations, for high-risk) → assumptions → delta with
  uncertainty → decision stability → limitations → headline estimate →
  full-artifact handle.
- **Answer** — a compact human-safe summary: what was evaluated, whether the
  evidence is usable, the decision it supports, and the caveats that block a
  stronger conclusion. The headline estimate leads the summary **only** for
  an `ok` artifact.

## The safety invariants

The profile enforces — and `check_invariants(artifact)` (plus the test
suite) verifies — two rules for every artifact:

1. **Never present `V_hat` without support diagnostics.** Any phase whose
   output contains the value estimate also contains a support-health item
   *earlier* in the list. The route phase never exposes the estimate.
2. **High-risk artifacts foreground caveats before estimates.** For a
   `high_risk` artifact, warnings and limitations are emitted before the
   value estimate in the interpret view, and the answer view withholds the
   estimate entirely.

`main()` asserts these invariants at runtime, so a regression breaks
`make example` rather than silently shipping unsafe context shaping.

## What's intentionally not here

- **Statistical computation.** No estimator, no confidence-interval math —
  the artifact already carries those numbers.
- **A hard dependency on a producer.** The profile reads a plain dict; it
  aligns with a `weaver-spec` `EvaluationArtifact` shape when one is
  available but does not require it.

## Read next

- [`docs/architectures/eval_artifact_profile.md`](../../../docs/architectures/eval_artifact_profile.md)
  is the public-docs version of this README.
- The [concepts guide](../../../docs/concepts.md) covers `ContextItem`,
  `Phase`, and the prompt renderer used here.

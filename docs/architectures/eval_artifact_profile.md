# Agent-safe evaluation-artifact profile

> Offline policy-evaluation reports are large and easy to misread. An agent
> that sees only a headline score such as `V_hat` can draw a confident,
> wrong conclusion. This profile compiles an evaluation artifact into
> **phase-aware, agent-safe** context that foregrounds support health,
> uncertainty, and caveats — and only then the headline estimate.

## TL;DR

| What | Where |
|---|---|
| The script | [`examples/architectures/eval_artifact_profile/main.py`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/eval_artifact_profile/main.py) |
| The fixtures | [`examples/architectures/eval_artifact_profile/fixtures/`](https://github.com/dgenio/contextweaver/tree/main/examples/architectures/eval_artifact_profile/fixtures) |
| Captured output | [`examples/architectures/eval_artifact_profile/OUTPUT.md`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/eval_artifact_profile/OUTPUT.md) |
| Local README | [`examples/architectures/eval_artifact_profile/README.md`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/eval_artifact_profile/README.md) |

Run it:

```bash
python examples/architectures/eval_artifact_profile/main.py
```

(Or `make architectures` / `make example`.)

## What it is (and isn't)

A **context-shaping profile**, not a statistics library. It does no
estimation — that belongs to the artifact producer (for example
`skdr-eval`). The profile, `compile_eval_context(artifact, phase)`, decides
*what to show an agent, in what order, at each phase*. Three fixtures
(`ok` / `caution` / `high_risk`) exercise it.

## Behaviour by phase

- **Route** — summary metadata only: artifact type, available diagnostics,
  whether it needs interpretation or policy gating. No metrics, no estimate.
- **Interpret** — the full diagnostic view: support health → warnings →
  (limitations, for high-risk) → assumptions → delta with uncertainty →
  decision stability → limitations → headline estimate → full-artifact handle.
- **Answer** — a compact human-safe summary: what was evaluated, whether the
  evidence is usable, the decision it supports, and the blocking caveats. The
  estimate leads the summary **only** for an `ok` artifact.

## The safety invariants

Enforced by the profile and verified by `check_invariants()` and the test
suite:

1. **Never present `V_hat` without support diagnostics.** Any phase whose
   output contains the value estimate also contains a support-health item
   *earlier* in the list; the route phase never exposes it.
2. **High-risk artifacts foreground caveats before estimates.**

`main()` asserts these at runtime, so a regression breaks `make example`
rather than silently shipping unsafe context shaping.

## Read next

- The [Concepts](../concepts.md) guide covers `ContextItem`, `Phase`, and
  the prompt renderer used here.
- The [context firewall](../context_firewall.md) is the complementary tool
  for keeping the *raw* artifact bytes out of the prompt.

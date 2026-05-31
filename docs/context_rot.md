# Context rot: more tools ≠ better routing

contextweaver's central claim is that **more context is not free** — past a
point it actively degrades a tool-using agent. This page makes that concrete
with a **deterministic, no-API-key** demo: it grows a tool catalog with
near-duplicate distractor tools and measures what the model actually has to
work with at each size.

![Context-rot curve: naive route-prompt tool count grows with the catalog while contextweaver stays flat at 5 ChoiceCards; contextweaver's correct-tool recall@5 erodes as distractor tools accumulate.](assets/context_rot.svg)

## What it measures

The full natural tool pool (83 tools) is kept present at every size, so the
evaluated query set — [`benchmarks/routing_gold.json`](https://github.com/dgenio/contextweaver/blob/main/benchmarks/routing_gold.json),
200 gold `query → expected tool` cases — stays constant. Only distractor
tools are added on top.

| Series | Meaning |
|---|---|
| **naive — tools visible to the model** | A "dump every schema" route prompt carries *all* tools. This line is the catalog size: it grows without bound. |
| **contextweaver — tools visible to the model** | The router returns at most `top_k` (= 5) `ChoiceCard`s regardless of catalog size. Flat. |
| **contextweaver — correct-tool recall@5** | Whether the right tool survives into that bounded shortlist. As distractors pile up, lexical routing recall erodes — this is the *rot*, measured rather than asserted. |

| Catalog size | Naive visible tools | contextweaver visible tools | Correct-tool recall@5 |
|---:|---:|---:|---:|
| 83 | 83 | 5 | 36.0 % |
| 166 | 166 | 5 | 28.5 % |
| 332 | 332 | 5 | 34.0 % |
| 664 | 664 | 5 | 17.5 % |
| 1328 | 1328 | 5 | 10.0 % |

The takeaway is two-sided and honest: contextweaver **bounds the model-visible
surface** (5 cards instead of 1,328 schemas — a 16× smaller route prompt at the
largest size), but lexical routing alone still feels distractor pressure
(recall@5 falls from 36 % to 10 %). That erosion is exactly why the router
supports stronger scorers — see the [embedding backend](tool_router.md) and the
[evaluation harness](https://github.com/dgenio/contextweaver/tree/main/src/contextweaver/eval).

## Reproduce it

```bash
pip install -e .
python scripts/context_rot_demo.py     # recompute the curve + re-render the SVG
make context-rot                        # same, via the Makefile
make context-rot-check                  # what CI runs: fail if the SVG drifts
```

The script is deterministic: the same catalog seed (`42`) and gold set always
produce the same curve, so the committed
[`benchmarks/results/context_rot.json`](https://github.com/dgenio/contextweaver/blob/main/benchmarks/results/context_rot.json)
and `docs/assets/context_rot.svg` are reproducible and CI-gated against drift.

## The live-model variant

This page deliberately measures a **routing-visibility proxy**, not end-task
answer accuracy with a real model — that keeps it deterministic and runnable in
CI. The complementary, real-model version (ask the same question under growing
distractor context, plot answer accuracy with and without contextweaver) lives
in the optional, credential-gated notebook
[`notebooks/context_rot_live.ipynb`](https://github.com/dgenio/contextweaver/blob/main/notebooks/context_rot_live.ipynb).
It is **not** run in CI (it needs an API key) and is tracked toward the public
real-model quality + cost benchmark in
[issue #345](https://github.com/dgenio/contextweaver/issues/345).

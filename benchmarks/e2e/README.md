# Adversarial end-to-end evaluation

Issue #445 replaces the weak question “does ContextWeaver beat dumping every tool into the prompt?” with a harder one:

> **Does ContextWeaver add meaningful value beyond strong current alternatives, and where does it lose?**

## Required arms

`benchmarks/adversarial_eval.py` makes all six arms first-class:

| arm | role |
|---|---|
| `naive_control` | Historical all-tools/full-history sanity control only. Never sufficient for a public competitive claim. |
| `simple_retrieval` | Cheap client-side lexical shortlist + recent history. Prevents generic retrieval value being attributed to ContextWeaver architecture. |
| `contextweaver_routing` | ContextWeaver shortlist with full history retained, isolating routing. |
| `contextweaver_full` | ContextWeaver routing plus budgeted context compilation. |
| `provider_native` | Current provider-native Tool Search/deferred-loading mechanism over the full catalog. Requires a real provider callback. |
| `contextweaver_plus_native` | Same provider-native mechanism after ContextWeaver bounds the candidate catalog. Requires the same real callback. |

The default deterministic run deliberately leaves the last two as `not_run`. A stub prompt is **not** an acceptable substitute for a provider-native feature.

## Credential-free mechanics check

```bash
python benchmarks/adversarial_eval.py
```

This exercises prompt construction, scoring, accounting, explicit missing-arm states and the “where ContextWeaver lost” section. It uses the deterministic stub model and is therefore **not publishable competitive evidence**.

The command can enforce that distinction:

```bash
python benchmarks/adversarial_eval.py --require-publishable
# exits non-zero until real provider-native arms are wired
```

## Real provider contract

A real benchmark driver imports the harness and supplies `provider_native_fn`:

```python
from benchmarks.adversarial_eval import ProviderObservation, run


def provider_native(task, offered_catalog, history):
    # Call the provider's actual current native Tool Search / deferred-loading API.
    # `offered_catalog` is the full catalog for provider_native and the
    # ContextWeaver-bounded catalog for contextweaver_plus_native.
    response = call_real_provider(task, offered_catalog, history)
    return ProviderObservation(
        chosen_tool=response.tool,
        answer=response.answer,
        prompt_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        latency_ms=response.latency_ms,
        cost_usd=response.cost_usd,
    )

report = run(
    call_fn=real_prompt_model_call,
    model="provider/model-id@YYYY-MM-DD",
    provider_native_fn=provider_native,
    dataset="held-out-dataset-v1",
)
```

The adapter must call the **actual provider-native mechanism**. Do not reimplement semantic search locally and label it native.

The provider callback receives only the catalog that the arm is allowed to expose. The harness scores a selected tool outside that catalog as unavailable/hallucinated.

## Measurement rules

- Offline prompt arms currently use the repository's explicit `heuristic/chardiv4` estimate for prompt-size mechanics.
- Real provider-native arms must report the provider API's actual usage fields and latency/cost where available.
- Do not compare unlike token units as if they were the same measurement; #841 is the regression that motivated this rule.
- Record exact model/API feature/version or date in the final report.
- Use multiple trials for stochastic real-model headline results; the deterministic stub is only a CI/mechanics fixture.

## Anti-cherry-picking

Before a public comparative result:

1. version the dataset/catalog and keep final evaluation cases held out from tuning;
2. include ambiguous, argument-correctness and large-result workflows;
3. include at least some externally sourced/adopter-derived sanitized cases;
4. do not drop an arm because it wins against ContextWeaver;
5. do not give ContextWeaver bespoke prompt tuning unavailable to the baseline without documenting it;
6. retain timeouts/failures rather than silently retrying them away;
7. publish the raw per-task/trial JSON alongside the aggregate;
8. preserve the generated **Where ContextWeaver lost** section.

## Current limitation

This foundation does not itself provide API credentials or claim a provider-native result. That is intentional. A publishable #445 report requires an opt-in real-model driver and current provider configuration; until then the repository should continue to describe those hypotheses as unverified in `docs/claims.md`.

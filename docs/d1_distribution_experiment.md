# D1 distribution experiment

This document operationalizes issues #855, #840, #658, and #551.

The question is not whether ContextWeaver can produce a capability snapshot.
The question is whether qualified users value the D1 workflow enough to keep it:

```text
snapshot -> inspect -> semantic diff -> verify
```

## What failure means

Do not collapse all non-adoption into one outcome.

| Observed result | Interpretation | Response |
|---|---|---|
| Too few qualified people actually saw D1 | Distribution inconclusive | Improve targeted reach once; do not add product surface. |
| People recognize the problem but do not understand/reach the receipt | Positioning/onboarding failure | Simplify the front door or path. |
| People reach first value but remove it | Product/value failure | Strong shrink/kill evidence. |
| People keep only `diff`, `inspect`, or one source adapter | Narrow pull | That slice becomes the candidate product. |
| Independent projects retain D1 | Survival signal | Continue only around the retained value. |

## Minimum funnel

Record:

```text
qualified exposure
  -> understood proposition
  -> chose to evaluate
  -> attempted setup
  -> reached first useful output
  -> used a real source/project
  -> retained independently / removed
```

A person who sees D1, understands it, and declines because Git/tests already
solve the problem is **high-value evidence**. They are not an adopter, but they
must not disappear from the experiment.

## Acquisition paths

Use multiple qualified paths rather than generic traffic:

- direct outreach to people who maintain agent/tool/API surfaces;
- GitHub discovery/participation where appropriate;
- professional network;
- relevant technical communities;
- technical content;
- organic discovery.

For D1, aim for roughly 10–20 qualified exposures and about five genuine
evaluation attempts if the exposure pool produces them. These are experiment
targets, not vanity quotas.

## Neutral discovery before the pitch

For design partners, ask about the current workflow first:

1. How do you know which effective tools/capabilities a deployed agent version had?
2. How do you detect and review MCP/OpenAPI/tool-definition changes before deployment?
3. Have schema/requiredness/rename/duplicate changes caused debugging or review friction?
4. How often does that happen and what does it cost?
5. Where are Git diff, configuration-as-code, and existing tests already sufficient?

Only after that, show D1 and ask whether the snapshot/diff receipt removes a
problem they already described.

## What to record

The machine-readable schema is `benchmarks/onboarding/schema.json`.
It captures:

- cohort and proposition;
- acquisition path / qualified exposure;
- comprehension and choice to evaluate;
- setup / first success / real-source usage;
- maintainer assistance;
- outcome and structured drop-off reason;
- retained narrow slice;
- 7/30-day retention state;
- optional privacy-safe version/commit/source metadata.

### Evidence-record consistency rules

The schema validates field shapes; the reporter also enforces cross-field
semantics so the funnel cannot contradict itself:

- funnel flags are monotonic: a later stage may be `true` only when every
  prerequisite stage is also `true`;
- `first_success=true` requires a setup attempt and a recorded
  `seconds_to_first_success`; that timing field must be absent otherwise;
- `real_source=true` requires an attempted setup;
- someone who did not choose to evaluate must have `outcome="declined"`, and a
  declined record must have `chose_to_evaluate=false`;
- `setup_failed` means setup was attempted but first success was not reached;
- `first_success`, `real_project`, `retained`, and `removed` outcomes require
  `first_success=true`;
- negative outcomes (`declined`, `setup_failed`, `removed`) require a structured
  `dropoff_reason` other than the sentinel `"none"`; non-negative outcomes must
  use `dropoff_reason="none"`.

These are part of the persisted D1 evidence contract, not merely reporting
conventions. Invalid records are rejected rather than silently normalized.

Participant IDs and notes are allowed for private/sanitized research records but
are never rendered by the aggregate reporter.

Generate aggregates with:

```bash
python scripts/onboarding_report.py
```

Synthetic fixtures are excluded by default.

## What not to count

Do not treat any of these as retained adoption:

- stars or forks;
- package downloads;
- clone counts;
- running the maintained fixture once;
- maintainer-created integrations;
- contributor activity;
- AI-authored examples;
- polite interview interest.

## Privacy

Do not collect or publish credentials, customer data, proprietary schemas,
private prompts, local paths, or unnecessary personal identifiers.
Public named adoption requires explicit permission.

The issue form `.github/ISSUE_TEMPLATE/d1_evaluation_report.yml` is suitable
only for information the evaluator is allowed to make public. Private interview
notes should remain private and contribute only sanitized structured aggregates.

## Decision

After competent distribution, update #758 with one of:

- **continue D1** — retained independent value;
- **shrink further** — only a narrow diagnostic/slice is retained;
- **D1 failed** — users reach value but do not keep it;
- **distribution inconclusive** — exposure/positioning was insufficient;
- **bounded D2/D3 experiment justified** — only if evidence independently
  surfaces those problems.

Do not answer a negative result by inventing a fourth product thesis.

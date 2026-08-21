# D1 evidence records

This directory may contain **sanitized structured records** conforming to
`../schema.json` when there is permission to retain them in the repository.
Most participant-level research should remain private and be represented here
only by privacy-safe aggregates.

Rules:

- never commit credentials, customer data, proprietary capability schemas,
  private prompts, local filesystem paths, or unnecessary personal identifiers;
- `participant_id` is optional and should use an anonymized cohort/project ID
  where possible;
- synthetic fixtures must set `synthetic: true` and never count as adoption;
- records for people who understood D1 and **declined to evaluate** are useful
  distribution/product evidence and may be represented with no setup attempt;
- funnel flags are monotonic: `understood_proposition` requires
  `qualified_exposure`, `chose_to_evaluate` requires understanding,
  `attempted_setup` requires choosing to evaluate, and `first_success` requires
  an attempted setup;
- `first_success=true` requires `seconds_to_first_success`; the timing field
  must be absent when first success is false;
- `real_source=true` requires an attempted setup;
- `declined` requires `chose_to_evaluate=false`; `setup_failed` requires an
  attempted setup without first success; `first_success`, `real_project`,
  `retained`, and `removed` outcomes require first success;
- negative outcomes (`declined`, `setup_failed`, `removed`) require a structured
  `dropoff_reason` other than `"none"`; all other outcomes must use
  `dropoff_reason="none"`;
- removed/former integrations are retained as negative evidence;
- public named adoption requires explicit permission and is tracked separately
  in `ADOPTERS.md`.

These cross-field rules are enforced by `scripts/onboarding_report.py`; invalid
records are rejected rather than silently normalized.

Generate the aggregate report with:

```bash
python scripts/onboarding_report.py
```

The default aggregate excludes synthetic records and never renders participant
IDs or free-text notes.

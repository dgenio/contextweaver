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
- removed/former integrations are retained as negative evidence;
- public named adoption requires explicit permission and is tracked separately
  in `ADOPTERS.md`.

Generate the aggregate report with:

```bash
python scripts/onboarding_report.py
```

The default aggregate excludes synthetic records and never renders participant
IDs or free-text notes.

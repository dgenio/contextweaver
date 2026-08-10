# Onboarding and retention evidence

This directory implements the measurement half of issues #658 and #551. It does **not** contain participant recruitment or claimed adoption by itself.

## What goes in `results/`

One JSON file per sanitized evidence run, conforming to [`schema.json`](schema.json).

Default reports exclude records with `"synthetic": true`.

Before committing a real record:

- use an anonymized `participant_id` unless public attribution is explicitly permitted;
- never include credentials, customer data, proprietary tool schemas/prompts, local filesystem paths, or internal incident detail that is not needed for the measurement;
- keep free-text `notes` sanitized;
- record the cohort accurately — a design partner who received synchronous help is **not** an unassisted evaluator;
- use `persistent_integration` only for a genuine project/workflow, not the maintained demo;
- record removed/abandoned integrations rather than deleting them from the evidence set.

Public named adopter evidence belongs in [`ADOPTERS.md`](../../ADOPTERS.md) only with explicit permission.

## Meaningful first success

`first_success=true` means the evaluator reached an actually useful ContextWeaver receipt, not merely installation or `--help`.

Allowed receipt categories include:

- source inventory;
- analysis finding (ambiguity/duplicate/coverage/etc.);
- compiled bundle identity/provenance;
- evaluation result;
- candidate diff/drift;
- runtime use of an evaluated surface.

## Generate the aggregate

```bash
python scripts/onboarding_report.py
```

This validates all records and writes:

- `benchmarks/onboarding/report.json`
- `benchmarks/onboarding/report.md`

The report is deliberately aggregate-only: participant IDs and notes are not rendered.

For a deterministic smoke test using the synthetic fixture:

```bash
python scripts/onboarding_report.py \
  --input benchmarks/onboarding/fixtures \
  --include-synthetic \
  --json-output /tmp/onboarding.json \
  --markdown-output /tmp/onboarding.md
```

## Interpretation

Keep these questions separate:

- **Design partners (#840):** is the underlying capability-surface problem painful before ContextWeaver is proposed?
- **Unassisted evaluators (#658):** can strangers reach a meaningful result without maintainer intervention?
- **Persistent integrations (#551/#658):** does genuine usage survive beyond the evaluation, ideally to ~30 days?

The target is a `go`, `narrow`, or `stop/rethink` decision in #758 — not maximizing the number of records.

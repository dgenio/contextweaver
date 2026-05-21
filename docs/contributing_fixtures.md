# Test Fixtures — Contributing Guide

This page documents the layout, normalisation, and regeneration policy
for the checked-in test fixtures under [`tests/fixtures/`](../tests/fixtures).

The fixtures back four issue-cluster regression suites that landed
together:

| Fixture set | Issue | Test file | What it pins |
|---|---|---|---|
| `tests/fixtures/golden/route_prompt/` | #296 | `tests/test_golden_prompts.py` | `ContextManager.build_route_prompt_sync` outputs (prompt, choice cards, route result, build stats) |
| `tests/fixtures/golden/mcp_ingestion/` | #296 | `tests/test_golden_mcp_ingestion.py` | `mcp_result_to_envelope` outputs across text / image / error scenarios |
| `tests/fixtures/weaver_spec/` | #295 | `tests/test_weaver_spec_fixtures.py` + `scripts/weaver_spec_conformance.py` | Round-trip + JSON-Schema validation for `SelectableItem`, `ChoiceCard`, `RoutingDecision`, `Frame` payloads |
| `tests/fixtures/sensitivity/` | #292 | `tests/test_sensitivity_fixtures.py` | `apply_sensitivity_filter` behaviour across all four sensitivity levels in both drop and redact modes |
| `tests/fixtures/context_explain/` | #291 | `tests/test_context_explanation.py` | `ContextBuildExplanation` shape produced by `ContextManager.build(..., explain=True)` |

## Layout conventions

Every fixture is a plain JSON file with **sorted keys** and **two-space
indentation** (the layout `tests/fixtures/_normalize.py:to_canonical_json`
produces).  Files should end with a trailing newline so `diff` and
editor folding behave predictably.

Fixtures are **content-addressable by file name**: each test discovers
its fixtures via `Path.glob("*.json")`.  Adding a new fixture is just
dropping a new JSON file in the right directory — no need to register
it anywhere.

## Normalisation policy

The shared helper `tests/fixtures/_normalize.py` strips volatile fields
before comparison so fixtures stay byte-stable across runs and
machines.  Three rules:

1. **Timestamps** (keys: `timestamp`, `created_at`, `updated_at`,
   `trace_id`) are replaced with `"<timestamp>"`.
2. **UUID-shaped ids** in keys like `id`, `decision_id`, `frame_id`,
   `selected_card_id`, `selected_item_id`, `request_id` are replaced
   with `"<prefix>-<uuid>"` — the human-readable prefix (e.g. `rd-`)
   is preserved so a failed-fixture diff still tells the reader which
   id field changed.
3. **Float leaves** are rounded to 4 decimal places by default (matches
   the rounding behaviour of `RouteTrace.to_dict()` and
   `CandidateExplanation.to_dict()`).  Pass `round_floats=None` to
   keep full precision when a fixture documents an explicit-precision
   field.

Adding a new normalisation rule is a **deliberate change** — only
canonicalise fields that are *known* to be volatile, otherwise tests
risk losing the ability to detect drift in real fields.

## Regeneration

There is intentionally **no autogeneration script**.  When a fixture
drifts:

1. Run the failing test (`pytest tests/test_golden_<name>.py -v`) and
   read the diff in the assertion message — every fixture test prints
   `expected (<path>)` vs `actual` for the full canonical payload.
2. If the drift is a *bug*, fix the bug and re-run.
3. If the drift is *intended* (e.g. a deliberate prompt format change),
   copy the `--- actual:` block from the assertion message into the
   fixture file.  Keep the canonical layout: sorted keys, two-space
   indent, trailing newline.

The test files include a small reference snippet at the top showing
how to capture a fresh canonical payload via
`tests/fixtures/_normalize.py:to_canonical_json` — use that helper
rather than `json.dumps(...)` to guarantee identical layout.

## CI integration

* Every fixture test runs in the standard `make test` / `make ci`
  gate.
* The weaver-spec fixture pass additionally runs through
  `scripts/weaver_spec_conformance.py --fixtures-dir tests/fixtures/weaver_spec`
  during the `Weaver-spec conformance` CI step (issue #295).  Failure
  messages cite the fixture file path so reviewers know which fixture
  to inspect or update.

## When to add a new fixture

Add a new fixture when:

* You change a public surface that produces JSON-shaped output
  (e.g. add a field to `BuildStats`, `RouteResult`, or `ChoiceCard`).
  A golden fixture pins the wire shape so downstream consumers see
  any drift.
* You fix a bug where the public output drifted without a test.
  Add the fixture that *would have* caught the bug.
* You add a new scenario (e.g. a new MCP content type) that the
  existing fixture set does not cover.

Do **not** add a new fixture for:

* Large conversations (snapshot them in a unit test instead).
* Intentionally unstable debug fields (use the normalizer's
  `drop_keys=` argument to scrub them).
* Network-dependent payloads (CI does not have network for
  application traffic; fixtures must be fully offline).

## Security note (sensitivity fixtures)

The `tests/fixtures/sensitivity/` set deliberately uses placeholder
content (`<<PLACEHOLDER>>`, `<<TEST_PLACEHOLDER_NEVER_A_REAL_SECRET>>`)
so the fixtures are safe to grep through and to ship in the public
repo.  Real secrets, credentials, or PII must **never** appear in
fixtures.  See `.claude/rules/sensitivity.md` for the broader
security-grade-code posture around `context/sensitivity.py`.

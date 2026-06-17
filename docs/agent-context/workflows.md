# Workflows

## Authoritative Commands

```bash
make fmt      # ruff format src/ tests/ examples/ scripts/
make lint     # ruff check src/ tests/ examples/ scripts/
make type     # mypy src/ examples/ scripts/  (examples + scripts gated too, #539)
make test     # pytest --cov=contextweaver --cov-report=term-missing -q
make example  # run all example scripts (includes architectures)
make architectures  # run reference architecture scripts under examples/architectures/
make demo     # python -m contextweaver demo
make ci       # fmt + lint + type + test + drift-check + module-size-check + doc-snippets-check + readme-version-check + example + demo
make drift-check  # one gate over every generated-artifact drift check (#522; in `make ci`)
make module-size-check  # enforce the ≤300-line convention, frozen baseline (#456; in `make ci`)
make doc-snippets-check # execute README + curated docs Python snippets (#526; in `make ci`)
make docs     # mkdocs build --clean (docs site — not part of CI)
make docs-serve  # mkdocs serve (live preview)
make benchmark        # run benchmark harness (non-gating; writes benchmarks/results/latest.json)
make benchmark-matrix # benchmark + per-backend × per-size matrix (#208) and per-namespace breakdown (#209)
make scorecard        # render benchmarks/scorecard.md from benchmarks/results/latest.json
make scorecard-check  # verify scorecard.md is up to date (gating CI step; exits non-zero on drift)
make sweep-scoring    # weight sweep for ScoringConfig (#214); writes benchmarks/sweep_scoring.md
make context-rot       # render context-rot demo JSON + docs/assets/context_rot.svg (#349)
make context-rot-check # verify context_rot.svg matches its committed JSON (gating CI step; exits non-zero on drift)
make readme-version-check  # verify README version refs and Python classifiers match sources (gating CI step; #347/#473)
make llms        # regenerate llms.txt and llms-full.txt from canonical docs
make llms-check  # verify llms.txt and llms-full.txt are up to date (gating CI step; exits non-zero on drift)
make gateway-scorecard-check  # verify gateway scorecard Markdown matches its committed JSON (gating CI step)
make record-demos-check  # verify committed asciinema casts match demo output (gating CI step)
make smoke-eval  # deterministic, credential-free smoke evaluation (non-gating CI step)
make weaver-conformance  # round-trip + JSON-Schema validate the weaver-spec adapter
                         # (fetches schemas from raw.githubusercontent.com; CI runs it as a gate)
```

> As of #474, `make ci` now mirrors the gating CI checks a contributor can run
> offline: the consolidated generated-artifact drift gate `make drift-check`
> (#522 — schemas, scorecards, recorded demos, llms.txt, context-rot SVG, and
> the public-API manifest #518), plus `make module-size-check` (#456),
> `make doc-snippets-check` (#526), and `make readme-version-check` (#347).
> The individual `*-check` targets still exist for granular use, but you no
> longer need to remember to run them separately before a PR.
>
> CI-only gates (not in `make ci` because they need the network or are heavy):
> `make weaver-conformance` (fetches schemas) and the docs build job.
> `make smoke-eval` (#392) and the benchmark job run in CI but remain non-gating.

`make ci` runs all declared targets in sequence. The CI-only gates above run on
every PR regardless; run them locally when the affected integrations change.

## Command-Selection Rules

| Goal | Command |
|---|---|
| Quick format check | `make fmt` |
| Quick lint check | `make lint` |
| Full validation | `make ci` (always — do not skip targets) |
| Run a single test | `pytest tests/test_<module>.py` or `pytest -k "test_name"` |
| Run all tests | `make test` |
| Verify examples work | `make example` |
| Interactive demo | `make demo` |
| Verify recorded demo casts | `make record-demos-check` |
| Build docs site | `make docs` |
| Live docs preview | `make docs-serve` |
| Run benchmark harness | `make benchmark` (non-gating; writes `benchmarks/results/latest.json`) |
| Run full per-backend × per-size matrix | `make benchmark-matrix` (#208 + #209) |
| Verify gateway benchmark scorecard | `make gateway-scorecard-check` |
| Run deterministic smoke evaluation | `make smoke-eval` (non-gating) |
| Run scoring-weight sweep | `make sweep-scoring` (#214; writes `benchmarks/sweep_scoring.md`) |
| Add an eval for a feature | follow [`.github/prompts/add-eval.prompt.md`](../../.github/prompts/add-eval.prompt.md) (#216) |
| Regenerate llms.txt / llms-full.txt | `make llms` (after editing canonical docs) |
| Check llms.txt / llms-full.txt for drift | `make llms-check` (exits non-zero if regeneration needed) |

**Do not** use `make test` alone as a validation gate. Always run `make ci` before declaring a change complete — it includes example and demo verification that catch integration issues `make test` misses.

## Setup (one-time)

```bash
pip install -e ".[dev]"
pre-commit install
```

Pre-commit hooks run `ruff format`, `ruff check --fix`, and file hygiene checks on every commit. Hooks may modify files — re-stage with `git add` if needed.

## Adding a Feature

1. Identify the relevant module (see module map in [AGENTS.md](../../AGENTS.md)).
2. Modify only the targeted module.
3. Update `protocols.py` if adding a new protocol.
4. Add tests in `tests/test_<module>.py`.
5. Run `make ci` — all declared targets must pass.
6. Update `CHANGELOG.md` under `## [Unreleased]`.
7. Add Google-style docstrings to any new public APIs.
8. Update examples/demos if the feature is user-facing.
9. Update agent-facing docs if the pipeline or public API changed.
10. **If the feature can move `recall@k` / `dropped` / `dedup_removed` /
    `prompt_tokens`**, follow
    [`.github/prompts/add-eval.prompt.md`](../../.github/prompts/add-eval.prompt.md)
    (#216) to extend the gold set or scenarios and regenerate
    `benchmarks/scorecard.md`. The sticky CI benchmark-delta comment
    (#211) surfaces any matrix-cell ⚠️ markers on the PR.

## Definition of Done

A change is complete when **all** of the following are true:

- [ ] `make ci` passes (all declared targets)
- [ ] `CHANGELOG.md` updated
- [ ] Google-style docstrings on all new public APIs
- [ ] Type hints on all new public functions and methods
- [ ] Tests added for new functionality
- [ ] Examples/demos updated if the feature is user-facing
- [ ] Agent-facing docs updated if pipeline, public API, or conventions changed

## Fixing a Bug

1. Write a failing test that reproduces the bug.
2. Fix the bug.
3. Run `make ci`.
4. Update `CHANGELOG.md`.
5. If the bug revealed a reusable lesson, record it per the process in [lessons-learned.md](lessons-learned.md).

## Adding a Store Backend

1. Implement the store class in `src/contextweaver/store/<name>.py`.
2. The class must implement the relevant protocol from `protocols.py`.
3. Export from `src/contextweaver/store/__init__.py`.
4. Add tests in `tests/test_store_<name>.py`.
5. Update `StoreBundle` if appropriate.

## Adding an Adapter

1. Create the adapter in `src/contextweaver/adapters/<protocol>.py`.
2. Pure stateless converter — no state, no core-type leakage.
3. External format dependencies stay at the adapter boundary.
4. Add tests in `tests/test_adapters.py`.
5. Add an example in `examples/`.

## Deprecating an API

Use the runtime machinery in `src/contextweaver/_deprecation.py` (issue #517);
do not call `warnings.warn` ad hoc.

1. Add a `Deprecation(name, since, removal, instead)` entry to the `_SHIMS`
   table in `_deprecation.py` (the single source of truth). Use the next minor
   for `since` and `"1.0.0"` for `removal` unless decided otherwise.
2. At the call site, emit the warning: `warn_deprecated("<name>")` inside a
   property/method/branch, or decorate a callable with
   `@deprecated("<name>", since=..., removal=..., instead=...)`. Keep behavior
   identical; route in-library callers through a private helper so the canonical
   path does not trip the warning.
3. Migrate every in-repo caller (src, examples, docs snippets, tests) off the
   deprecated surface; intentional shim tests assert the warning with
   `pytest.warns(DeprecationWarning)`. The `filterwarnings` gate in
   `pyproject.toml` escalates first-party deprecations to errors, so a leftover
   caller fails CI.
4. Add the surface to the inventory table in `docs/upgrading.md` and a
   `CHANGELOG.md` "Deprecated" entry naming the replacement.

**Documentation-only exception.** Do **not** add a runtime warning when the only
call site would live in a module barred from side effects — a re-export-only
`__init__.py` (hard rule: only re-exports) or a pure-data module such as
`types.py` (invariant: no side effects in the data layer), or an internal
serialization key on a hot path. Keep the surface a plain alias/accessor, skip
steps 1–2 (no `_SHIMS` entry, no `warn_deprecated`), and record it as a
documentation-only row in `docs/upgrading.md`. The `ToolCard` alias is the
reference example.

## Documentation Governance

### When docs must be updated

- Any PR that changes the context pipeline stages, routing pipeline, or public API.
- Any PR that adds, removes, or renames a module.
- Any PR that changes project conventions, commands, or the definition of done.

### Who triggers updates

- The author of the PR is responsible for updating docs in the same PR.
- Reviewers should check the [review checklist](review-checklist.md) for doc-update requirements.

### Resolving contradictions

If two docs disagree:
1. `AGENTS.md` is authoritative for agent guidance and shared rules.
2. `docs/architecture.md` is authoritative for architecture detail.
3. `Makefile` is ground truth for command definitions.
4. Source code is ground truth for implementation details.

Fix the less-authoritative source to match.

### Promoting lessons into canonical docs

When a lesson from [lessons-learned.md](lessons-learned.md) represents a durable pattern:
1. Add it to the appropriate canonical doc (`AGENTS.md`, `invariants.md`, or `workflows.md`).
2. Keep the lesson entry but mark it as promoted with a cross-reference.

### Avoiding duplicate authority

Each piece of guidance should have exactly one canonical home. Use cross-references instead of copies. Exception: hard rules (the 2 auto-reject items) may be briefly restated in tool-specific override files for visibility, since those files may be the only context an agent loads.

### Updating navigation tables

When adding a new canonical doc under `docs/agent-context/`, update the navigation tables in all three routing files:
- `AGENTS.md` (Documentation Map)
- `.github/copilot-instructions.md` (Canonical References)
- `.claude/CLAUDE.md` (Canonical References)

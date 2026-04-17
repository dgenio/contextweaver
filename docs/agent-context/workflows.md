# Workflows

## Authoritative Commands

```bash
make fmt      # ruff format src/ tests/ examples/
make lint     # ruff check src/ tests/ examples/
make type     # mypy src/
make test     # pytest --cov=contextweaver --cov-report=term-missing -q
make example  # run all example scripts
make demo     # python -m contextweaver demo
make ci       # fmt + lint + type + test + example + demo  (6 targets)
make docs     # mkdocs build --clean (docs site — not part of CI)
make docs-serve  # mkdocs serve (live preview)
make benchmark   # run benchmark harness (non-gating; writes benchmarks/results/latest.json)
make llms        # regenerate llms.txt and llms-full.txt from canonical docs
make llms-check  # verify llms.txt and llms-full.txt are up to date (exits non-zero on drift)
```

`make ci` runs all six targets in sequence. It is the single validation gate.

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
| Build docs site | `make docs` |
| Live docs preview | `make docs-serve` |
| Run benchmark harness | `make benchmark` (non-gating; writes `benchmarks/results/latest.json`) |
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
5. Run `make ci` — all six targets must pass.
6. Update `CHANGELOG.md` under `## [Unreleased]`.
7. Add Google-style docstrings to any new public APIs.
8. Update examples/demos if the feature is user-facing.
9. Update agent-facing docs if the pipeline or public API changed.

## Definition of Done

A change is complete when **all** of the following are true:

- [ ] `make ci` passes (all 6 targets)
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

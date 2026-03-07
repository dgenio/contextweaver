# contextweaver — Claude Operating Guide

> **`AGENTS.md` is the single source of truth** for shared rules, conventions,
> module map, and architectural guidance. This file contains only Claude-specific
> operating behavior. Do not use this file as a standalone repo handbook.

## Hard Rules (auto-reject)

1. **No `print()` in library code.** Use hooks or logging. `__main__.py` is exempt.
2. **No business logic in `__init__.py`.** Only re-exports.

## Explore Before Acting

- Read `AGENTS.md` for the module map, conventions, and pipeline summary before
  modifying any source file.
- Read `docs/agent-context/invariants.md` before proposing structural changes,
  simplifications, or consolidations.
- Inspect the specific area of the repo you plan to change. Do not infer
  repo-wide conventions from a single local example.
- Check `.claude/rules/` for scoped rules relevant to the files you are editing.
- Prefer canonical docs and repository evidence over assumptions or cached patterns.

## Implement Safely

- Preserve invariants and architectural intent. Invariants take priority over
  cleanup, simplification, or local refactors.
- Do not invent conventions. If a rule is not in `AGENTS.md` or
  `docs/agent-context/`, it does not exist.
- Use authoritative commands from `docs/agent-context/workflows.md`. Do not guess
  alternative commands or skip `make ci` targets.
- Check `AGENTS.md` "Things That Must Not Be Simplified" before proposing
  consolidation of protocols, serialization, or pipeline stages.
- Follow path conventions in `AGENTS.md` — especially the async/sync boundary
  between `context/` and `routing/`.

## Validate Before Completing

- Run `make ci` as the validation gate — it runs 6 targets (fmt, lint, type,
  test, example, demo). `make test` alone is not sufficient.
- Check whether your change triggers a doc update. PRs that change the pipeline,
  public API, module map, conventions, or workflows must update `AGENTS.md`
  or `docs/agent-context/`.
- Verify scoped impact: if you touched sensitivity, store protocols, pipeline
  stages, or `__init__.py` files, check the corresponding invariants in
  `docs/agent-context/invariants.md`.

## Handle Contradictions

- If canonical docs disagree with each other: `docs/architecture.md` is
  authoritative for architecture; `AGENTS.md` is authoritative for agent
  guidance; `Makefile` is ground truth for commands; source code is ground
  truth for implementation.
- If Claude-specific rules contradict canonical docs, flag the contradiction
  explicitly. Do not silently pick one side.
- If you find stale or conflicting guidance, surface it to the user rather
  than working around it.
- When uncertain, default to preserving existing behavior and flagging the
  uncertainty.

## Lessons Learned and Promotion

- During work, note candidate lessons — patterns where the obvious approach
  was wrong or a constraint was non-obvious.
- A candidate lesson is reusable only if a different agent would make the same
  mistake on a different change. One-off incidents are not lessons.
- Promotion order: update canonical docs (`AGENTS.md`, `docs/agent-context/`)
  first. Update Claude-specific files second, only if Claude needs an
  operational overlay.
- Do not promote a fresh observation into durable guidance after one
  occurrence. Wait for a pattern to recur or for a clear generalizable rule.
- If a Claude-specific rule becomes clearly shared and durable, propose
  promoting it to canonical docs and then reducing it here.
- See `docs/agent-context/lessons-learned.md` for the failure-capture workflow
  and existing durable lessons.

## Debugging

1. `make lint` — style and import errors.
2. `make type` — type errors.
3. `make test` — test suite.
4. Check `BuildStats` fields to understand what the context engine dropped and why.
5. Use `ContextManager.artifact_store.list_refs()` to inspect intercepted tool outputs.

## Running Tests

```bash
pip install -e ".[dev]"     # one-time setup
pytest -q                    # all tests
pytest tests/test_<mod>.py   # single module
pytest -k "test_name"        # single test
```

## Scoped Rules

Check `.claude/rules/` for path-triggered rules. Currently:

| File | Scope | Purpose |
|---|---|---|
| `rules/sensitivity.md` | `context/sensitivity.py` | Security-grade code caution |

## Canonical References

| Topic | File |
|---|---|
| Shared rules, conventions, module map | `AGENTS.md` |
| Architecture and tradeoffs | `docs/agent-context/architecture.md` |
| Invariants and forbidden shortcuts | `docs/agent-context/invariants.md` |
| Workflows and definition of done | `docs/agent-context/workflows.md` |
| Lessons learned | `docs/agent-context/lessons-learned.md` |
| Review checklist | `docs/agent-context/review-checklist.md` |
| Full pipeline and module detail | `docs/architecture.md` |
| Core concepts | `docs/concepts.md` |

## Update Order

1. Shared durable knowledge → canonical docs first (`AGENTS.md`, `docs/agent-context/`).
2. Claude-specific projections → this file and `.claude/rules/` second.
3. If a new lesson is incident-specific, do not promote it into durable docs yet.
4. If a Claude rule becomes shared and durable, promote it to canonical docs,
   then reduce or remove it here.

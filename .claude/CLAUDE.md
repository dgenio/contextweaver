# contextweaver — Claude Operating Guide

> **`AGENTS.md` is the single source of truth** for shared rules, conventions,
> module map, and architectural guidance. This file contains only Claude-specific
> operating behavior. Do not use this file as a standalone repo handbook.

## Hard Rules

Do not duplicate a second hard-rule list here. Read the canonical classifications
and enforcement status in `AGENTS.md` and `docs/agent-context/invariants.md`.
If this file and the canonical files disagree, the canonical files win and this
projection must be corrected.

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

- Preserve mechanically enforced invariants and documented review-policy
  contracts. Do not claim a prose-only rule is mechanically enforced.
- Do not invent conventions. If a rule is not in `AGENTS.md` or
  `docs/agent-context/`, it does not exist.
- Use authoritative commands from `docs/agent-context/workflows.md` and the
  `Makefile`; do not guess alternative targets.
- Before consolidating protocols, serialization, pipeline stages, or other
  architectural seams, check `docs/agent-context/invariants.md`.
- Respect layer direction: adapter-specific integration code belongs at the
  boundary; core code must not import `contextweaver.adapters` to hide a cycle.
- Treat `ContextManager`'s public behavior/API as the contract. Its private
  mixin composition is an implementation detail, not an invariant.

## Validate Before Completing

- Run `make ci` as the repository validation gate. **Do not hard-code a target
  count here**; the Makefile is the ground truth and the gate evolves.
- Check whether your change triggers a doc update. PRs that change the pipeline,
  public API, module map, conventions, or workflows must update `AGENTS.md`
  or `docs/agent-context/`.
- Verify scoped impact: if you touched sensitivity, store protocols, pipeline
  stages, layer boundaries, or public API, check the corresponding invariant
  and its stated enforcement mechanism.
- If a hard invariant lacks the test/gate claimed by its docs, fix the docs or
  add the gate rather than treating prose as proof.

## Async/sync guidance

The context build/selection core is synchronous computation. Public/runtime
integration may provide sync/async entry points and store bridges where I/O
requires them; routing remains synchronous pure computation. Do not make new
core context logic async merely to satisfy an obsolete "async-first" slogan.
Follow the actual contracts and tests in the touched subsystem.

## Handle Contradictions

- `AGENTS.md` is authoritative for shared agent guidance;
  `docs/agent-context/invariants.md` is authoritative for invariant status;
  `Makefile` is ground truth for commands; source plus tests are ground truth
  for shipped implementation behavior.
- `docs/architecture.md` and `docs/agent-context/architecture.md` provide design
  explanation, but neither silently overrides a newer explicit invariant or the
  shipped behavior.
- If Claude-specific rules contradict canonical docs, fix/flag the projection;
  do not silently pick the easier instruction.
- If canonical docs contradict shipped code, surface and reconcile the mismatch
  instead of training future agents to copy whichever side they happened to read.
- When uncertain, preserve existing behavior and make the uncertainty explicit.

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
6. Enable `logging.DEBUG` on `contextweaver.context` to trace pipeline stages.
7. Enable `logging.DEBUG` on `contextweaver.routing` to trace beam search.

## Running Tests

```bash
pip install -e ".[dev]"     # one-time setup
pytest --cov=contextweaver --cov-report=term-missing -q  # all tests
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
| Invariants and enforcement status | `docs/agent-context/invariants.md` |
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

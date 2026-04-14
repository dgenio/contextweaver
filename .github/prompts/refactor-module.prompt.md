---
mode: agent
description: Step-by-step workflow for extracting or refactoring an oversized module in contextweaver.
---

# Refactor a Module

Follow these steps in order. **No behavior changes allowed** — structural cleanup only.

## References
- `AGENTS.md` — module map, hard rules, strong patterns, ≤300-line guideline
- `.github/instructions/context.instructions.md` — context engine invariants
- `.github/instructions/routing.instructions.md` — routing engine invariants
- `.github/instructions/sensitivity.instructions.md` — security-grade sensitivity code
- `docs/agent-context/invariants.md` — hard constraints and forbidden shortcuts

## Steps

1. **Confirm the module exceeds the size guideline.** Run `wc -l src/contextweaver/<module>.py`.
   Exempt modules: `types.py`, `envelope.py`, `__main__.py`. _Success: non-exempt file ≥300 lines._

2. **Identify a cohesive unit to extract.** Group related functions or classes with no
   circular dependencies on the remainder. Consult `docs/agent-context/invariants.md` —
   do not split invariant-coupled stages (e.g., `dependency_closure`).
   _Success: extracted unit is clearly bounded._

3. **Create the new module.** Start with `from __future__ import annotations`. Add type
   hints and Google-style docstrings on all public APIs. No business logic in `__init__.py`.
   _Success: new file created; `make lint && make type` pass._

4. **Move code and update all imports.** Remove moved code from the source module.
   Search for all references: `grep -r "<symbol>" src/ tests/ examples/` and update each
   import site. _Success: no `ImportError`; `make type` passes._

5. **Update `__init__.py` re-exports.** Keep any previously-public symbol re-exported
   from the same package path. _Success: public API is unchanged._

6. **Run `make ci` — no behavior changes allowed.**
   ```
   make ci
   ```
   _Success: all targets (`fmt lint type test example demo`) exit 0._

7. **Verify both modules satisfy the size guideline.**
   Run `wc -l src/contextweaver/<source>.py src/contextweaver/<extracted>.py`.
   _Success: both files are ≤300 lines (or are exempt)._

8. **Update `AGENTS.md` module map and `CHANGELOG.md`.** Add the new module to the table
   in `AGENTS.md` and record the refactor under `## [Unreleased]`.
   _Success: docs reflect the new structure._

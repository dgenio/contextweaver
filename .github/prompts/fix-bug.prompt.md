---
mode: agent
description: Step-by-step workflow for fixing a bug in contextweaver.
---

# Fix a Bug

Follow these steps in order. Each step has a clear success criterion.

## References
- `AGENTS.md` — module map, hard rules, strong patterns, coding style
- `.github/instructions/context.instructions.md` — context engine invariants
- `.github/instructions/routing.instructions.md` — routing engine invariants
- `.github/instructions/sensitivity.instructions.md` — security-grade sensitivity code
- `docs/agent-context/invariants.md` — hard constraints and forbidden shortcuts

## Steps

1. **Write a failing test that reproduces the bug.**
   Add the test to `tests/test_<module>.py` before touching production code.
   _Success: `make test` fails only on the new test._

2. **Identify the root-cause module.**
   Use `AGENTS.md`'s module map and `docs/agent-context/architecture.md` to trace the
   defect to its origin. Check `docs/agent-context/invariants.md` to confirm the fix
   won't violate a hard constraint. _Success: root-cause file and line range are known._

3. **Apply the minimal fix.**
   Change only the lines required to resolve the defect. Do not refactor unrelated code
   in the same commit. If touching `context/sensitivity.py`, apply extra scrutiny per
   `.github/instructions/sensitivity.instructions.md`. _Success: fix is self-contained._

4. **Verify the previously-failing test now passes.**
   ```
   make test
   ```
   _Success: the new test passes; no previously-passing test regresses._

5. **Update `CHANGELOG.md`.**
   Add a concise entry under `## [Unreleased]`. _Success: entry is present._

6. **Run `make ci` and verify all checks pass.**
   ```
   make ci
   ```
   _Success: all targets (`fmt lint type test example demo`) exit 0._

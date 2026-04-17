---
mode: agent
description: Step-by-step workflow for adding a new feature to contextweaver.
---

# Add a Feature

Follow these steps in order. Each step has a clear success criterion.

## References
- `AGENTS.md` — module map, hard rules, strong patterns, coding style
- `.github/instructions/context.instructions.md` — context engine invariants
- `.github/instructions/routing.instructions.md` — routing engine invariants
- `.github/instructions/sensitivity.instructions.md` — security-grade sensitivity code
- `docs/agent-context/invariants.md` — hard constraints and forbidden shortcuts

## Steps

1. **Identify the target module.** Consult the module map in `AGENTS.md`. If no existing
   module fits, propose a new one that satisfies the ≤300-line guideline.
   _Success: module path is known._

2. **Check current module size.** Run `wc -l src/contextweaver/<module>.py`. If the
   feature would push a non-exempt module over 300 lines, stop and open a refactor issue
   first. _Success: target module will remain ≤300 lines after the change._

3. **Add the feature code.** Apply coding-style rules from `AGENTS.md`:
   `from __future__ import annotations`, type hints, Google-style docstrings, no `print()`
   in library code, exceptions from `contextweaver.exceptions` only.
   _Success: `make lint && make type` pass._

4. **Update `protocols.py` if needed.** If the feature introduces a new protocol interface,
   add it to `protocols.py`. _Success: no new Protocol defined outside `protocols.py`._

5. **Add tests.** Create or extend `tests/test_<module>.py`. Use real in-memory
   implementations — do not mock internal modules. Use `pytest.mark.asyncio` for async paths.
   _Success: `make test` passes with new tests included._

6. **Update `CHANGELOG.md`.** Add a concise entry under `## [Unreleased]`.
   _Success: entry is present._

7. **Update agent-facing docs if invariants changed.** If the feature changes the pipeline,
   public API, or conventions, update `AGENTS.md` and/or `docs/agent-context/`.
   _Success: docs match code._

8. **Run `make ci` and verify all checks pass.**
   ```
   make ci
   ```
   _Success: all targets (`fmt lint type test example demo`) exit 0._

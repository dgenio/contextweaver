# Lessons Learned

This is not an incident archive. It captures reusable patterns from past mistakes
and defines the process for converting incidents into durable guidance.

## Failure-Capture Workflow

When a bad change is caught in review or causes a regression:

1. **Identify the root cause** — was it a missing rule, a misunderstood boundary, or a documentation gap?
2. **Determine if it's reusable** — would a different agent make the same mistake on a different change? If yes, it's a lesson. If no, it's a one-off incident — don't record it here.
3. **Generalize the lesson** — write it as a pattern, not a narrative. "Don't do X because Y" is better than "on date Z, agent A did X to file B."
4. **Choose the right home:**
   - If it's a hard constraint → add to [invariants.md](invariants.md)
   - If it's a workflow fix → add to [workflows.md](workflows.md)
   - If it's an architectural insight → add to [architecture.md](architecture.md)
   - If it's a recurring trap that doesn't fit elsewhere → add to this file
5. **If promoted**, keep the entry here but mark it: "**Promoted →** [target file](link)."

## What Belongs Here

- Recurring mistakes that agents make across different changes
- Generalized lessons with clear "do this instead" guidance
- Patterns where the obvious approach is wrong

## What Does Not Belong Here

- One-off incidents tied to specific dates, PRs, or files
- Narrative history of past bugs
- Lessons that have been fully captured in invariants, workflows, or architecture docs (mark as promoted instead)

## Durable Lessons

### 1. Pipeline stage count drift

**Mistake:** Docs described a 7-stage context pipeline; the actual implementation has 8 stages (missing `dependency_closure`).

**Lesson:** When modifying pipeline documentation, always verify stage count and order against the source code (`context/manager.py`). Do not copy pipeline descriptions from other docs without verification.

**Generalized rule:** Treat pipeline stage documentation like API documentation — verify against implementation, not against other docs.

### 2. "Simplification" proposals that break design intent

**Mistake:** Proposing to merge `serde.py` with per-class `to_dict()`/`from_dict()`, or to collapse store protocols into concrete classes, or to make routing async for "consistency."

**Lesson:** Before proposing a simplification, check [invariants.md](invariants.md) for the "Things That Must Not Be Simplified" section. If the thing you want to simplify is listed, it exists for a reason. Read the rationale before proposing changes.

**Generalized rule:** Things that look redundant in this codebase often exist for extensibility or correctness. Check invariants before proposing consolidation.

### 3. Overstatement in documentation

**Mistake:** "Zero-dependency is a hard constraint" (overstated — extras are acceptable). "Always use X" for things that are strong patterns, not hard rules.

**Lesson:** Distinguish hard rules (auto-reject, 2 items) from strong patterns (recommended, judgment applies). Overstated rules cause agents to either (a) reject valid changes or (b) ignore all rules after discovering false mandates.

**Generalized rule:** Use precise language in constraints. "Must" and "always" should be reserved for actual invariants. Use "prefer" or "strongly recommended" for patterns.

### 4. Module map staleness

**Mistake:** `envelope.py` added in an early version but never added to the module map in agent-facing docs. Agents couldn't find `ResultEnvelope`, `BuildStats`, etc.

**Lesson:** When adding a new module, update the module map in `AGENTS.md` in the same PR.

**Generalized rule:** Treat the module map as part of the public API surface. New modules require map updates just like new functions require docstrings.

### 5. `make ci` composition drift

**Mistake:** `AGENTS.md` described `make ci` as 4 targets when the Makefile runs 6.

**Lesson:** Do not describe command composition from memory. Check the `Makefile` for ground truth.

**Generalized rule:** For command documentation, the build system file (`Makefile`, `pyproject.toml`) is always ground truth.

## Update Triggers

Record a new lesson when:
- A review catches a mistake that a well-documented rule would have prevented.
- The same category of mistake recurs across multiple changes or agents.
- A documentation gap directly causes a bad change.

Do not record lessons for:
- Typos, formatting issues, or trivial errors.
- One-off issues that are unlikely to recur.

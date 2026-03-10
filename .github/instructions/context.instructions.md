---
applyTo: src/contextweaver/context/**
---

# Context Engine — Agent Instructions

Path-scoped guidance for `src/contextweaver/context/`. Read before modifying any file here.

## Pipeline stage ordering (must not be reordered)

`ContextManager.build()` executes exactly these 8 stages in order:

1. `generate_candidates` (`candidates.py`) — phase + policy filter over event log
2. `resolve_dependency_closure` (`candidates.py`) — pull in parent items via `parent_id`
3. `apply_sensitivity_filter` (`sensitivity.py`) — drop/redact by sensitivity level
4. `apply_firewall_to_batch` (`firewall.py`) — intercept raw `tool_result` text
5. `score_candidates` (`scoring.py`) — recency + Jaccard token overlap + kind priority + token penalty
6. `deduplicate_candidates` (`dedup.py`) — near-duplicate removal
7. `select_and_pack` (`selection.py`) — budget-aware token selection
8. `render_context` (`prompt.py`) — final prompt assembly

**Never reorder these stages.** Stages 2 and 4 have hard ordering constraints:
dependency closure must run before scoring (ancestors must be scoreable), and the
firewall must run before scoring (summaries, not raw text, must be scored).

## Firewall invariants

- Raw `tool_result` text **never** reaches the prompt. `apply_firewall` replaces
  `item.text` with a summary and stores the raw bytes in `ArtifactStore`.
- The artifact handle is always `f"artifact:{item.id}"`.
- `item.artifact_ref` is set on every firewall-processed item.
- Do not bypass `apply_firewall_to_batch` or move raw text past stage 4.
- See `firewall.py` and `docs/agent-context/invariants.md` for full rationale.

## Async-first pattern

- `build()` is `async`. The sync entry point is `build_sync()`, which is a thin
  `asyncio.run(self.build(...))` wrapper.
- All new pipeline stages must be `async` with `_sync` wrappers where needed.
- Do not introduce blocking I/O inside `async` pipeline functions.

## Dependency closure

- `resolve_dependency_closure()` (stage 2) walks `item.parent_id` chains and
  adds missing ancestors to the candidate list.
- **Must run before scoring and deduplication.** Removing or skipping it produces
  incoherent context: tool results appear without their tool calls.
- Closure count is tracked in `BuildStats.closures_added`.

## `manager.py` size and decomposition

- `manager.py` is currently ~876 lines, which exceeds the ≤300-line module
  guideline. Decomposition is tracked in dgenio/contextweaver#73 and
  dgenio/contextweaver#69.
- Do not add new methods to `ContextManager` until the decomposition is complete.
- Prefer adding new logic to an existing focused module (e.g. `candidates.py`,
  `scoring.py`) and calling it from the manager.

## Sensitivity enforcement

- `sensitivity.py` is security-grade code. Changes require extra review scrutiny.
- Never weaken the default sensitivity floor or default drop action.
- See `.github/instructions/sensitivity.instructions.md` for full rules.

## Import rules

- Raise custom exceptions from `contextweaver.exceptions`, not bare `ValueError`
  or `RuntimeError`.
- Text similarity utilities (`tokenize`, `jaccard`, `TfIdfScorer`) must be
  imported from `contextweaver._utils` — never duplicated here.
- Use `from __future__ import annotations` in every source file.

## Related issues

- dgenio/contextweaver#73 — `manager.py` decomposition (large file)
- dgenio/contextweaver#69 — context pipeline refactor
- dgenio/contextweaver#63 — context firewall design

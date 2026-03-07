# contextweaver — Copilot Instructions

> **`AGENTS.md` is the single source of truth** for shared rules, conventions, and
> module layout. This file contains review-critical rules that must be visible in
> GitHub and Copilot-specific guidance. Do not treat this file as a standalone reference.

## Review-Critical Rules

- **Review code and agent docs together.** When a PR changes behavior that agent-facing
  docs describe (pipeline stages, public API, conventions, module layout), review the
  code change and the doc update as a unit. If the PR has no doc update, flag it.
- **PRs that change any of these must trigger doc review:**
  workflows, invariants, architecture intent, review rules, path-specific conventions,
  or the module map. Expect a corresponding update to `AGENTS.md` or `docs/agent-context/`.
- **Invariants take priority** over cleanup, simplification, or local refactors.
  Check `docs/agent-context/invariants.md` before proposing structural changes.
- **Do not invent conventions.** All conventions must be grounded in `AGENTS.md`,
  `docs/agent-context/`, or verifiable repository evidence. If you cannot find a rule,
  it does not exist — do not assume one.
- **Use authoritative commands.** Run `make ci` (not `make test` alone) as the
  validation gate. See `AGENTS.md` for the full command reference.
- **Surface contradictions.** If you find conflicting guidance between docs, or between
  docs and code, flag the contradiction explicitly. Do not silently pick one side.

## Hard Rules (auto-reject)

These cause automatic rejection in review. No exceptions.

1. **No `print()` in library code.** Use hooks or logging. `__main__.py` (CLI) is exempt.
2. **No business logic in `__init__.py`.** Only re-exports allowed.

## Copilot-Specific Guidance

**Understand system-level intent before making changes.** contextweaver has deliberate
architectural boundaries (async context vs sync routing, protocol-based stores, 8-stage
pipeline, complementary serialization layers). Changes that are locally correct but
violate these boundaries will be rejected. Before modifying code, check:

- `AGENTS.md` → module map, conventions, "things that must not be simplified"
- `docs/agent-context/architecture.md` → design boundaries and tradeoffs
- `docs/agent-context/invariants.md` → hard constraints and forbidden shortcuts

## Architecture (minimal orientation)

- **Context Engine** — async-first, 8-stage pipeline. See `AGENTS.md` for the stage list.
- **Routing Engine** — sync-only, pure computation. Do not make async.
- **Sensitivity enforcement** (`context/sensitivity.py`) — security-grade code.
  Never weaken defaults. See `.github/instructions/sensitivity.instructions.md`.
- **Zero runtime deps** in core (`install_requires` is empty). Optional extras are acceptable.

## Canonical References

| Topic | File |
|---|---|
| Shared rules, conventions, module map | `AGENTS.md` |
| Architecture detail and tradeoffs | `docs/agent-context/architecture.md` |
| Hard constraints and forbidden shortcuts | `docs/agent-context/invariants.md` |
| Authoritative commands and workflows | `docs/agent-context/workflows.md` |
| Recurring lessons | `docs/agent-context/lessons-learned.md` |
| Review checklist | `docs/agent-context/review-checklist.md` |
| Core concepts glossary | `docs/concepts.md` |
| Full pipeline and module detail | `docs/architecture.md` |

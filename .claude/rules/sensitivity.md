# Sensitivity Enforcement — Security-Grade Code

> **Scope:** `src/contextweaver/context/sensitivity.py`
> **Consult when:** editing sensitivity.py or any code interacting with
> sensitivity enforcement, redaction hooks, or sensitivity floor configuration.
> **Why this file exists:** sensitivity enforcement is security-grade code that
> requires extra caution beyond general repo conventions.

- **Never weaken defaults.** The default sensitivity floor and default drop action
  are deliberately conservative. Relaxing them can silently expose data.
- **Extra scrutiny required.** Treat changes to this module as security-sensitive.
  Verify that no redaction path is bypassed or weakened.
- **Do not refactor for simplicity.** The current structure enforces safety
  invariants. See `docs/agent-context/invariants.md` for rationale.
- **Canonical rule:** `AGENTS.md` path conventions, `docs/agent-context/invariants.md`.

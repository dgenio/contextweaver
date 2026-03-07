---
applyTo: src/contextweaver/context/sensitivity.py
---

# Sensitivity Enforcement — Security-Grade Code

This module enforces data classification and redaction in the context pipeline.
Treat all changes as security-sensitive:

- **Never weaken defaults.** The default sensitivity floor and default drop action
  are deliberately conservative.
- **Extra review scrutiny required.** Changes here affect what data reaches the LLM.
- **Do not refactor for "simplicity."** The current structure exists to enforce
  safety invariants. See `docs/agent-context/invariants.md` for rationale.

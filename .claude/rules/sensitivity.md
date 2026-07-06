# Sensitivity Enforcement — Security-Grade Code

> **Scope:** `src/contextweaver/context/sensitivity.py`, `src/contextweaver/secrets.py`,
> and every prompt-bound scrub path — `routing/cards.py` (`item_to_card` /
> `make_choice_cards` `redact_secrets`), `adapters/_bounded_browse.py` (shared
> tool + resource/prompt browse), and `context/firewall_api.py`
> (`compact_tool_result` `redact_secrets`).
> **Consult when:** editing any of the above, or any code interacting with
> sensitivity enforcement, secret scrubbing, redaction hooks, or sensitivity
> floor configuration.
> **Why this file exists:** sensitivity enforcement is security-grade code that
> requires extra caution beyond general repo conventions. Scrubbing that lands
> on only one of several parallel card/firewall paths is a recurring bug class
> (issue #743): keep the scrub call in the *shared* helper, never a copy.

- **Never weaken defaults.** The default sensitivity floor and default drop action
  are deliberately conservative. Relaxing them can silently expose data.
- **Extra scrutiny required.** Treat changes to this module as security-sensitive.
  Verify that no redaction path is bypassed or weakened.
- **Do not refactor for simplicity.** The current structure enforces safety
  invariants. See `docs/agent-context/invariants.md` for rationale.
- **Canonical rule:** `AGENTS.md` path conventions, `docs/agent-context/invariants.md`.

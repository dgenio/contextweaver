# Sensitivity & Redaction

Sensitivity enforcement is contextweaver's security-grade subsystem. It decides
which context items may reach a prompt and which are dropped or redacted first.
This page is the operator's configuration manual: the levels, the floor/action
knobs, how to write a redaction hook, how enforcement interacts with the
firewall and drilldown, how to verify it, and its limits.

> Misconfiguration here is a data-exposure risk. Every behavioural claim below
> is checked against the source; do not assume defaults are more permissive than
> stated.

## The four levels

`Sensitivity` is an ordered enum (lowest → highest):

| Level | Meaning (suggested) |
|---|---|
| `public` | Safe to send anywhere. **Default for unlabelled items.** |
| `internal` | Team-internal; not for external model providers without review. |
| `confidential` | Sensitive business/PII data. |
| `restricted` | Credential-shaped / regulated content; the tightest class. |

Map these onto your organisation's classification scheme; the ordering is what
enforcement relies on, not the names.

## Floor and action

Enforcement is driven by two `ContextPolicy` fields:

- `sensitivity_floor` (default **`confidential`**): items **at or above** the
  floor are enforced.
- `sensitivity_action` (default **`drop`**): what enforcement does — `"drop"`
  removes the item entirely; `"redact"` replaces its text via the configured
  redaction hooks and keeps a scrubbed placeholder.

```python
from contextweaver import ContextManager
from contextweaver.config import ContextPolicy
from contextweaver.types import Sensitivity

# Redact (not drop) anything confidential-or-higher, using the built-in mask hook.
policy = ContextPolicy(
    sensitivity_floor=Sensitivity.confidential,
    sensitivity_action="redact",
    redaction_hooks=["mask"],
)
manager = ContextManager(policy=policy)
```

| Goal | floor | action |
|---|---|---|
| Drop anything sensitive silently (default) | `confidential` | `drop` |
| Keep structure but scrub sensitive text | `confidential` | `redact` |
| Only ever drop credential-shaped content | `restricted` | `drop` |

The defaults are deliberately conservative. **Do not weaken them** without
review — see the sensitivity rule in the repo's agent guidance.

## Labelling: don't rely on defaults

Unlabelled items default to `public`, so enforcement never sees content the
caller forgot to classify. Two mechanisms raise labels before enforcement:

- **`HeuristicSensitivityClassifier`** (opt-in, deterministic) inspects item
  text and *raises* the label to `restricted` for credential-shaped content or
  `confidential` for PII-shaped markers (email/SSN/card). It can only raise,
  never lower. `contextweaver mcp serve` enables it by default (secure-by-default,
  issue #744); pass it to a library `ContextManager` explicitly:

  ```python
  from contextweaver.context.classify import HeuristicSensitivityClassifier
  manager = ContextManager(sensitivity_classifier=HeuristicSensitivityClassifier())
  ```

- **`redact_secrets=True`** runs a deterministic secret-scrubbing pass over
  firewall summaries and extracted facts before they reach the prompt.

## Redaction hooks

`sensitivity_action="redact"` applies the hooks named in
`ContextPolicy.redaction_hooks`, in order. Two are built in and registered at
import:

- `"mask"` — `MaskRedactionHook`: replaces the item's text with a masked
  placeholder and drops its `artifact_ref` so the rendered prompt cannot
  advertise a handle that drilldown could dereference back to the original.
- `"secret"` — `SecretRedactor`: substring-scrubs secret shapes from the text
  (complements, does not replace, the mask hook).

Register your own hook (it must implement the `RedactionHook` protocol —
`redact(item) -> ContextItem`):

```python
from dataclasses import replace

from contextweaver.context.sensitivity import register_redaction_hook
from contextweaver.types import ContextItem


class BlankRedactor:
    """Mirror MaskRedactionHook's contract: replace text, clear the artifact ref,
    and stamp metadata["redacted"] so the handle can't be dereferenced back."""

    def redact(self, item: ContextItem) -> ContextItem:
        metadata = dict(item.metadata)
        metadata["redacted"] = True
        return replace(item, text="[REDACTED]", artifact_ref=None, metadata=metadata)


register_redaction_hook("blank", BlankRedactor())
# then reference it: ContextPolicy(sensitivity_action="redact", redaction_hooks=["blank"])
```

## How it interacts with the rest of the pipeline

- **Filter runs before the firewall.** In the context pipeline, the sensitivity
  filter (stage 3) runs *before* `apply_firewall` (stage 4), so sensitive
  payloads are dropped/redacted before any summariser or extractor sees them.
- **Drilldown cannot launder content back in.** `ContextManager.drilldown`
  enforces the floor against the artifact's source item: recovering the raw
  bytes of a dropped/redacted item raises `PolicyViolationError` unless
  `ContextPolicy.allow_redacted_drilldown=True` (issue #451).
- **Gateway `tool_view` is governed by the policy gate, not this filter.**
  Gateway artifacts are stored unredacted at rest; bound raw egress with a
  `meta_tool: tool_view` rule in the [security model](security_model.md), not by
  assuming they are scrubbed.

## Verifying your configuration

The repo ships classification fixtures under `tests/fixtures/sensitivity/`
(`public`, `internal`, `confidential`, `restricted`, `pii_like`, `secret_like`).
Use them (or your own) to assert that your floor/action behave as intended, and
add a test that a known-sensitive payload never appears in a built pack's
rendered prompt.

## Limitations

- **Enforcement is label-dependent.** Without a classifier, an unlabelled
  sensitive item is treated as `public`. Enable
  `HeuristicSensitivityClassifier` (or label at ingest) for defence in depth.
- **No content inspection by default.** The classifier is pattern-based and
  deterministic — it is not a full DLP engine and will miss novel secret shapes.
- **`redact` keeps structure.** Redaction replaces text but the item still
  occupies a slot; use `drop` when the item must not appear at all.
- Sensitivity routing does not authenticate users or authorize tool execution —
  those remain host/upstream responsibilities (see
  [Security Model](security_model.md)).

# Skill Cards as Context Artifacts

> The Weaver projects need a clear boundary between *reusable guidance produced
> by one component* and *context selected by another component*. A reviewed
> **skill card** is guidance authored and approved elsewhere; contextweaver's
> job is only to decide whether — and how — that guidance enters a given
> phase's prompt.
>
> This page documents the mapping. It is documentation, not a new adapter:
> everything below uses existing contextweaver APIs and adds no dependency.

## What a skill card is

A skill card is a small, reviewed unit of guidance — "when editing
`sensitivity.py`, treat changes as security-grade", or "prefer `match`
statements for protocol dispatch". Some other component (a reviewer, a curation
pipeline) decides the card is worth keeping. contextweaver receives the card as
data and scores it against the current query under the same budget pressure as
every other event.

## The mapping

A skill card maps onto a single [`ContextItem`](https://github.com/dgenio/contextweaver/blob/main/src/contextweaver/types.py).
There is no bespoke type — the existing fields carry everything a card needs:

| Skill-card concept | `ContextItem` field | Notes |
|---|---|---|
| artifact id | `id` | Stable, unique. Reuse the card's own id so provenance round-trips. |
| artifact text | `text` | The guidance itself — what the model should read. |
| scope metadata | `metadata["scope"]` | Where the card applies (module, task type, phase hint). Free-form. |
| sensitivity metadata | `sensitivity` | One of `Sensitivity.public` / `internal` / `confidential` / `restricted`. Checked against the active `ContextPolicy.sensitivity_floor` — items below the floor are dropped or redacted. |
| provenance metadata | `metadata["provenance"]` | Who reviewed it, when, source commit/URL. Audit trail, not scored. |
| task-matching notes | `text` (+ `metadata`) | Matching is lexical against `text`; see below. |

`kind` should be `ItemKind.doc_snippet` for reference guidance (or
`ItemKind.policy` for high-priority guidance — `policy` carries a higher kind
priority in scoring, but inclusion is still subject to per-kind limits
(`max_items_per_kind`) and the phase token budget, so it is not guaranteed to
appear in any given prompt). If a rule must always be present, inject it via the
pack's `header`/`footer` or size the phase budget to accommodate it. Both kinds
flow through the standard phase-filter → sensitivity → firewall → scoring →
dedup → budget pipeline with no special-casing.

### Constructing a card

```python
from contextweaver.types import ContextItem, ItemKind, Sensitivity

card = ContextItem(
    id="skill:sensitivity-is-security-grade",
    kind=ItemKind.doc_snippet,
    text=(
        "When editing context/sensitivity.py, treat changes as security-grade: "
        "never weaken the default sensitivity floor or default drop action."
    ),
    sensitivity=Sensitivity.internal,
    metadata={
        "scope": {"module": "context/sensitivity.py", "task": "edit"},
        "provenance": {
            "reviewed_by": "maintainers",
            "source": "AGENTS.md#things-that-must-not-be-simplified",
        },
    },
)
```

Ingest it like any other event:

```python
from contextweaver.context.manager import ContextManager

mgr = ContextManager()
mgr.ingest_sync(card)
```

## Task matching

contextweaver does **not** run a separate skill-card matcher. The card competes
in the normal scoring pass: its `text` is scored against the current query with
tokenized Jaccard overlap, combined with tag overlap (`metadata["tags"]`),
recency, item-kind priority, and a token-cost penalty (see
[`context/scoring.py`](https://github.com/dgenio/contextweaver/blob/main/src/contextweaver/context/scoring.py)).
This is **not** TF-IDF or BM25 — those are *routing* backends, a separate
subsystem. The highest-scoring items that fit the phase budget are kept. Two
consequences worth designing for:

- **Matching quality is a function of the card's `text`.** Put the words a
  query would use into the guidance itself; do not hide the trigger only in
  free-form `metadata` such as `scope`, which is not scored. (The one scored
  metadata field is `metadata["tags"]`, which contributes tag overlap.)
- **`metadata["scope"]` is yours to enforce.** contextweaver preserves it but
  does not filter on it. If you want a card to apply only to one module, read
  `metadata["scope"]` in your own pre-ingest filter and skip cards that do not
  apply before calling `ingest_sync`.

### Matching example

Query: *"I'm editing context/sensitivity.py — anything I should know?"*

The card above scores well: its `text` shares the `editing`, `context`,
`sensitivity.py`, `sensitivity`, and `context/sensitivity.py` tokens with the
query (contextweaver's `tokenize()` helper lowercases input and splits
compounds on `.`, `/`, and `-`, but does **not** stem — so write cards and
queries with overlapping word forms). With little competition from other items
on this query, the card survives scoring and lands in the compiled context.
The model sees the security-grade warning before it proposes a change.

### Non-matching example

Query: *"How do I add a new CrewAI adapter?"*

The same card scores near zero — no lexical overlap with adapter/CrewAI terms —
so it is dropped under budget pressure in favour of adapter-relevant items. The
card stays in the event log (nothing is deleted); it simply does not enter
*this* prompt. That is the intended behaviour: guidance is available everywhere
but only surfaces where it is relevant.

## See also

- [How contextweaver Fits](interop.md) — the overall policy-vs-execution boundary
- [Concepts](concepts.md) — `ContextItem`, phases, and the scoring pipeline
- [External Memory Backends](integration_memory.md) — the related pattern for
  cross-session facts and episodes

# weaver-spec ↔ contextweaver type mapping

This page documents how [`weaver_contracts`](https://github.com/dgenio/weaver-spec)
types map to and from contextweaver's internal types. It accompanies the
adapter module
[`contextweaver.adapters.weaver_contracts`](../src/contextweaver/adapters/weaver_contracts.py)
and the `[weaver-spec]` optional extra.

## Installation

```bash
pip install 'contextweaver[weaver-spec]'
```

## Compatibility target

contextweaver tracks `weaver_contracts >= 0.2.0, < 1.0` (any MAJOR=0 release;
the spec promises no breaking changes within a major version). The CI step
`weaver-spec conformance` exercises the adapter against the published schemas
at `https://weaver-spec.dev/contracts/v0/` on every PR.

## Name-clash note

The spec and contextweaver reuse two type names with different semantics:

| Name | weaver-spec | contextweaver |
|---|---|---|
| `SelectableItem` | A single *menu option* (id / label / description / capability_id / metadata) | A *full tool definition* (kind, name, description, schemas, examples, tags, cost_hint, etc.) |
| `ChoiceCard` | A *menu of N options* (`items: list[SelectableItem]`) | A *single compact rendered card* (1:1) — never carries args schema |

The adapter bridges this by:

1. Storing contextweaver-specific fields under
   `metadata["_contextweaver"]` so a `cw → spec → cw` round-trip is lossless.
2. Wrapping a contextweaver `ChoiceCard` (1) as a spec `ChoiceCard` (N=1) when
   converting individual cards.
3. Grouping a contextweaver `RoutingDecision.choice_cards` list into a single
   spec `ChoiceCard` menu under one spec `RoutingDecision`.

## Public adapter surface

```python
from contextweaver.adapters.weaver_contracts import (
    # SelectableItem (tool def) ↔ spec SelectableItem (menu option)
    to_weaver_selectable_item,
    from_weaver_selectable_item,
    # ChoiceCard (1) ↔ spec ChoiceCard (N)
    to_weaver_choice_card,       # single CW card → one-option spec menu
    to_weaver_choice_cards,      # list of CW cards → spec menu
    from_weaver_choice_card,     # spec menu → list of CW cards
    from_weaver_choice_card_single,  # spec menu (exactly 1) → CW card
    # RoutingDecision ↔ RoutingDecision (1:1, but with internal regrouping)
    to_weaver_routing_decision,
    from_weaver_routing_decision,
    # ResultEnvelope ↔ Frame
    to_weaver_frame,
    from_weaver_frame,
)
```

## Field-by-field mapping

### `SelectableItem`

| contextweaver | weaver-spec | Notes |
|---|---|---|
| `id` | `id` | Direct copy. Must be non-empty. |
| `name` | `label` | The LLM-facing short label. |
| `description` | `description` | Must be non-empty. |
| `namespace` | `capability_id` (prefix) | `to_weaver` emits `"{namespace}:{name}"` when `namespace` is set, otherwise just `id`. `from_weaver` infers `namespace` from `capability_id.split(":", 1)[0]` when there's no `_contextweaver` payload. |
| `kind`, `tags`, `args_schema`, `output_schema`, `examples`, `constraints`, `side_effects`, `cost_hint` | `metadata["_contextweaver"][...]` | Preserved verbatim. |
| `metadata` (other keys) | `metadata` (other keys) | User keys pass through untouched alongside `_contextweaver`. |

### `ChoiceCard`

| contextweaver | weaver-spec | Notes |
|---|---|---|
| `id` | `items[0].id` (single wrap) or `items[i].id` (group) | The card itself becomes a menu *option*, not a menu. |
| `name` | `items[i].label` | |
| `description` | `items[i].description` | |
| `namespace`, `name` | `items[i].capability_id` | `"{namespace}:{name}"` when `namespace` set. |
| `tags`, `kind`, `has_schema`, `cost_hint`, `side_effects`, `score` | `items[i].metadata["_contextweaver"][...]` | `score` is omitted when `None`. |
| `to_weaver_choice_card`: spec menu `id` | — | Defaults to `f"menu:{card.id}"`; override with `menu_id=`. |
| `to_weaver_choice_cards`: spec menu `id` | — | Required argument. |
| — | `context_hint` | Optional pass-through on both directions. |

### `RoutingDecision`

| contextweaver | weaver-spec | Notes |
|---|---|---|
| `id` | `id` | Required, non-empty. |
| `choice_cards` (`list[ChoiceCard]`, may be empty) | `choice_cards` (`list[ChoiceCard]`, min 1) | A non-empty list of CW cards is grouped into a single spec menu whose `id` defaults to `f"{decision.id}:menu"`. An empty CW list raises `CatalogError` because the spec requires ≥ 1 menu. |
| `timestamp` (`datetime`) | `timestamp` | Naive timestamps are coerced to UTC. JSON form is ISO 8601. |
| `selected_item_id`, `selected_card_id`, `context_summary` | same names | `None` values are omitted from JSON output to comply with the schema's "field absent" semantics. |
| `metadata` | `metadata` | Pass-through; the adapter does not write to `metadata["_contextweaver"]` at this level (the per-card extras live inside the spec menu's items). |

### `Frame` ↔ `ResultEnvelope`

The spec's `Frame` has three required fields with no preimage in
`ResultEnvelope`: `frame_id`, `capability_id`, `created_at`. The caller
supplies them.

| contextweaver `ResultEnvelope` | weaver-spec `Frame` | Notes |
|---|---|---|
| (caller-supplied) | `frame_id` | Required, non-empty. |
| (caller-supplied) | `capability_id` | Required, non-empty. Typically `"{namespace}:{tool_name}"`. |
| (caller-supplied or `datetime.now(timezone.utc)`) | `created_at` | Naive timestamps are coerced to UTC. |
| `summary` (may be empty) | `summary` (must be non-empty) | Empty input becomes `"(no summary)"` to satisfy the spec; the adapter remembers the original under `_contextweaver.original_summary` so the round-trip is lossless. |
| `status` | `structured_data["status"]` | Spec doesn't have a status field; stored alongside facts. |
| `facts` | `structured_data["facts"]` | |
| `views` | `structured_data["views"]` | Serialized via `ViewSpec.to_dict()`. |
| `artifacts` (`list[ArtifactRef]`) | `handle_refs` (`list[str]`) + `metadata["_contextweaver"]["artifacts"]` | The spec only carries handle strings; full `ArtifactRef` metadata (media type, size, label) is preserved in `_contextweaver` for the round trip. |
| `provenance["redaction_notes"]` | `redaction_notes` | Pass-through. |
| `provenance` (full) | `metadata["_contextweaver"]["provenance"]` | |

### Foreign-origin frames

`from_weaver_frame` is also valid on `Frame` instances produced outside
contextweaver (no `_contextweaver` metadata key). In that case:

- `status` defaults to `"ok"`.
- `facts`, `views` default to empty lists.
- `artifacts` is reconstructed from `handle_refs` with placeholder
  `ArtifactRef(handle=h, media_type="application/octet-stream", size_bytes=0)`
  entries.
- `provenance` carries `redaction_notes` when the source set them.

## Verifying conformance

Round-trip + JSON-Schema validation runs in CI on every PR. To reproduce
locally:

```bash
make weaver-conformance
```

This fetches the published schemas, exercises every `to_weaver_*` / `from_weaver_*`
pair, and validates the JSON form of `SelectableItem`, `ChoiceCard`,
`RoutingDecision`, and `Frame` against the corresponding schema.

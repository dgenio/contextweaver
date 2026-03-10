---
applyTo: src/contextweaver/routing/**
---

# Routing Engine — Agent Instructions

Path-scoped guidance for `src/contextweaver/routing/`. Read before modifying any file here.

## ChoiceGraph validation invariants (`graph.py`)

`ChoiceGraph._validate()` enforces four rules — all must hold at all times:

1. **Root exists** — `root_id` must be present in `_nodes`.
2. **Children resolve** — every edge destination must exist in `_nodes | _items`.
3. **No cycles** — `topological_order()` must succeed (raises `GraphBuildError` if not).
4. **All items reachable** — every item in `_items` must be reachable from `root_id`.

Cycle detection is **eager**: `add_edge()` calls `_creates_cycle()` immediately and
raises `GraphBuildError` before the edge is persisted. Do not bypass this check.

Serialisation via `from_dict()` rebuilds `children` / `child_types` from `_edges`
to guarantee consistency — never rely on serialised node metadata for child lists.

## TreeBuilder grouping strategies (`tree.py`)

`TreeBuilder.build()` tries three strategies in priority order:

1. **Namespace grouping** — group by first dot-segment of `item.namespace`;
   requires ≥ 50 % of items to have a namespace and ≥ 2 groups.
2. **Jaccard clustering** — farthest-first seeding over `tokenize(_text_repr(item))`;
   falls back if clustering yields < 2 groups.
3. **Alphabetical fallback** — sort by `item.name.lower()`, split into even chunks.

The builder is **deterministic**: it sorts items by `item.id` before processing.
Do not introduce randomness or non-deterministic ordering inside `_build_subtree`.

Every node has at most `max_children` children (default 20). Oversized groups are
coalesced via `_coalesce_groups()` or re-split before adding edges.

## Router beam-search constraints (`router.py`)

- **Deterministic tie-breaking**: children are sorted `(-score, id)` — descending
  score, alphabetical ID for ties. Never change this sort key.
- `confidence_gap` (default 0.15) widens the beam by 1 when rank-1 and rank-2
  scores differ by less than the gap. Must stay in `[0.0, 1.0]`.
- Results are ranked `(-score, item_id)` — same determinism guarantee end-to-end.
- The TF-IDF index is lazily built on first `route()` call via `_ensure_index()`.
  Items are indexed by sorted `item_id` before non-leaf nodes; do not change order.
- Fallback scoring (nodes not in TF-IDF index) uses `jaccard()` from
  `contextweaver._utils` — never duplicate this logic here.

## Catalog invariants (`catalog.py`)

- Item IDs must be unique within a `Catalog`; `register()` raises `CatalogError`
  on duplicates.
- `generate_sample_catalog(n, seed=42)` is seeded for reproducibility. The default
  seed **must not change** — demos and tests depend on deterministic output.
- `Catalog.hydrate()` returns **shallow copies** of `args_schema`, `examples`, and
  `constraints`. Callers must not mutate the returned dicts; use `copy.deepcopy`
  if mutation is needed.

## ChoiceCard constraints (`cards.py`)

- `ChoiceCard` must **never** include a full argument schema. It is a compact,
  LLM-friendly summary; full schemas are hydrated on demand via `Catalog.hydrate()`.
- Keep card text representation minimal to avoid consuming prompt tokens.

## Synchronous-only routing

- The entire routing engine is **synchronous** (pure computation, DAG traversal,
  beam search). Do not introduce `async`/`await` anywhere in `routing/`.
- The engine has zero runtime dependencies on the context engine — do not import
  from `contextweaver.context.*` inside `routing/`.

## Import rules

- Raise custom exceptions from `contextweaver.exceptions` (`GraphBuildError`,
  `RouteError`, `CatalogError`, `ItemNotFoundError`), not bare exceptions.
- Text similarity (`tokenize`, `jaccard`, `TfIdfScorer`) must come from
  `contextweaver._utils`.
- Use `from __future__ import annotations` in every source file.

## Related issues

- dgenio/contextweaver#73 — module size tracking
- dgenio/contextweaver#69 — routing refactor work
- dgenio/contextweaver#63 — ChoiceGraph design and validation

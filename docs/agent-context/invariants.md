# Invariants

These are the constraints that must not be broken. Violations are review blockers.

## Hard Rules (auto-reject)

These cause automatic rejection in review. No engineering judgment — they are absolute.

1. **No `print()` in library code.** Use hooks or logging. `__main__.py` (CLI) is exempt.
2. **No business logic in `__init__.py`.** Only re-exports allowed.

## Must-Preserve Constraints

### Zero runtime dependencies

Core `install_requires` must remain empty. The library is stdlib-only for Python ≥ 3.10. Optional dependency groups via extras (e.g., `[dev]`, future `[llm]`) are acceptable.

### Async/sync boundary

Context Engine (`context/`) is async-first with `_sync` wrappers. Routing Engine (`routing/`) is sync-only. This split is intentional — routing is pure computation with no I/O. Do not unify them.

### 8-stage context pipeline

The pipeline stages must remain in this exact order:

1. `generate_candidates` → 2. `dependency_closure` → 3. `sensitivity_filter` →
4. `apply_firewall` → 5. `score_candidates` → 6. `deduplicate_candidates` →
7. `select_and_pack` → 8. `render_context`

Stage reordering breaks correctness (see [architecture.md](architecture.md) for why-this-order).

### Dependency closure

If a selected `ContextItem` has a `parent_id`, the parent must be included in the final context even if it scored lower. Without this, tool results can appear without their tool calls, producing incoherent context. Do not remove or bypass.

### Append-only event log

The event log is append-only. Mutate only via `InMemoryEventLog.append()`. Direct mutation breaks the audit trail and consistency invariants.

### Determinism

All core pipelines must be deterministic. Tie-break by ID, sorted keys. No randomness in pipeline stages.

## Forbidden Shortcuts

### Do not collapse protocols into concrete classes

Store protocols exist for backend extensibility. The protocol layer in `protocols.py` is separate from the `InMemory*` implementations in `store/` by design. Merging them locks the library to in-memory backends.

### Do not consolidate serialization into `serde.py` alone

<a name="serialization-design"></a>

`serde.py` provides shared primitives (enum handling, optional-field handling). Per-class `to_dict()` / `from_dict()` methods handle class-specific serialization logic. They are complementary:

- `serde.py` = shared helpers (used by multiple classes)
- `to_dict()` / `from_dict()` = class-specific encapsulation

Consolidating all serialization into `serde.py` removes encapsulation. Removing per-class methods and using `dataclasses.asdict()` loses custom serialization logic.

### Do not weaken sensitivity defaults

`context/sensitivity.py` is security-grade code. The default sensitivity floor (`confidential`) and default action (`drop`) are deliberately conservative. Never weaken these defaults without explicit security review.

### Do not add I/O to the data layer

`types.py`, `envelope.py`, `config.py`, `serde.py`, and `exceptions.py` are pure data — no I/O, no side effects. Adding I/O (file reads, network calls, logging) to these modules breaks the layered architecture.

## Safe vs Unsafe Simplifications

| Change | Safe? | Why |
|---|---|---|
| Add a field to an existing dataclass | Usually safe | Follow `to_dict`/`from_dict` pattern, add default value |
| Add a new store protocol method | Safe | Existing backends won't break if the method has a default impl |
| Merge two pipeline stages | **Unsafe** | Each stage has a single responsibility; merging creates coupling |
| Replace protocols with ABCs | **Unsafe** | Breaks structural typing; forces inheritance on custom backends |
| Inline `_utils.py` helpers into calling code | **Unsafe** | Creates duplicate similarity logic |
| Move types from `envelope.py` to `types.py` | **Unsafe** | `envelope.py` exists to keep result types separate from input types |
| Remove `ViewRegistry` | **Unsafe** | Breaks progressive disclosure for large artifacts |

## Cross-Cutting Rules

- **Module size ≤ 300 lines** — exempt: `types.py`, `envelope.py`, `__main__.py`.
- **`from __future__ import annotations`** — every source file.
- **Google-style docstrings** — every public class and function.
- **Type hints** — every public function and method.
- **Custom exceptions only** — from `contextweaver.exceptions`, not bare Python exceptions.

## Update Triggers

Update this file when:
- A new hard constraint is established by the maintainer.
- A forbidden shortcut is discovered through a bad change.
- A safe/unsafe determination changes due to architectural evolution.
- A cross-cutting rule is added or relaxed.

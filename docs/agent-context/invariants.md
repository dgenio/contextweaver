# Invariants

This file separates **mechanically enforced invariants** from **review policy**.
A hard invariant must name the test/gate that enforces it. If a constraint has
no mechanical check, it is review policy and must not be described as if CI
proves it automatically.

## Mechanically enforced invariants

### Canonical MCP `tool_id` round-trip

MCP-facing adapter surfaces that opt into the canonical gateway ID contract
must emit IDs that round-trip through `parse_tool_id` / `format_tool_id`.
The canonical contract carries namespace, name, optional version, and schema
hash identity; the legacy hand-formatted `mcp:{name}` form is not valid for
those surfaces.

Framework adapters are a separate, deliberately loose identifier class. IDs
such as `crewai:{name}`, `langchain:{name}`, or `openapi:{name}` identify an
adapter-local capability and are **not** required to parse as canonical MCP
`tool_id` values unless that adapter explicitly adopts the canonical contract.
Do not silently broaden one ID class into the other.

**Enforcement:** `tests/test_tool_id.py` plus the MCP adapter/gateway tests.

### Module-size ratchet

Ordinary source modules must remain at or below **500 lines**. Named exemptions
remain explicit; pre-existing modules above 500 are frozen at their recorded
ceiling and may shrink but not grow past it.

The limit is a maintainability signal, not a decomposition target. A contributor
must not create a grab-bag helper or duplicate logic merely to move a cohesive
module below the limit. When a split is needed, split along a real responsibility
or dependency boundary.

**Enforcement:** `make module-size-check` / `scripts/check_module_size.py` and
`scripts/module_size_baseline.json`.

### Public API manifest

Changes to the supported public package surface must be intentional and keep the
checked-in API manifest in sync.

**Enforcement:** `make api-check` through `make drift-check` / `make ci`.

### Generated-artifact consistency

Generated schemas, API manifests, scorecards, and other registered generated
artifacts must match their canonical inputs.

**Enforcement:** `make drift-check` through `make ci`, with the individual
registered `*-check` commands documented in `AGENTS.md`.

## Must-preserve contracts

The constraints below are product/architecture contracts. Each names the tests
or gate that exercises the behavior where a focused mechanical gate exists;
otherwise it is explicitly marked **review policy**.

### Minimal core dependencies

Core dependencies (`pyproject.toml` `dependencies`) must stay small, audited,
and broadly useful to the primary surfaces. Heavy or runtime-specific packages
belong under optional dependencies unless a maintainer explicitly accepts the
core cost.

**Enforcement:** dependency-resolution/floor CI jobs exercise compatibility;
whether a new dependency is justified is **review policy**.

### Sync core, bridged public execution

The Context Engine's core build/selection pipeline is synchronous computation.
Public/runtime integration may expose synchronous and asynchronous entry points
and store bridges where I/O requires them. Routing remains synchronous pure
computation. Do not force core context logic into `async` merely to satisfy a
naming convention, and do not remove the supported async-store bridge contracts.

**Enforcement:** context manager/build tests plus async-store bridge tests;
architectural placement is **review policy**.

### Context pipeline ordering

The context build stages remain:

1. `generate_candidates`
2. `dependency_closure`
3. `sensitivity_filter`
4. `apply_firewall`
5. `score_candidates`
6. `deduplicate_candidates`
7. `select_and_pack`
8. `render_context`

Reordering can change correctness and security semantics.

**Enforcement:** context build/manager test suites exercise the pipeline output;
the exact architectural stage boundary is **review policy** unless a dedicated
ordering test is added.

### Dependency closure

If a selected `ContextItem` depends on a parent through `parent_id`, the build
must preserve the required parent relationship so a tool result is not surfaced
without the context needed to understand it.

**Enforcement:** context selection/build tests.

### Append-only event log

The event log is append-only through the store protocol. Callers must not mutate
stored history behind the protocol.

**Enforcement:** store protocol/conformance tests; direct source-level mutation
avoidance is **review policy**.

### Determinism

Core pipelines must be deterministic for identical inputs/configuration: stable
ordering, stable tie-breaking, and no implicit randomness.

**Enforcement:** deterministic/golden/benchmark tests across the context and
routing suites; introducing a new nondeterministic dependency is **review
policy** unless separately gated.

### `ContextManager` public surface, not its mixin layout

`ContextManager`'s supported public method surface and behavior are the contract.
The current `_IngestMixin` / `_BuildMixin` / `_RoutingMixin` decomposition is a
private implementation detail and may be simplified, recomposed, or replaced as
long as the public contract remains compatible.

Do **not** preserve a mixin merely because issue #101 once used it to satisfy an
old line-count target.

**Enforcement:** public API manifest/gates plus `ContextManager` behavioral tests.
Private class composition is **not** an invariant.

### Layer direction: core must not depend on adapters

`adapters/` owns external/protocol integration surfaces. Core modules under
`context/`, `routing/`, stores, data/config, and other provider-neutral layers
must not import `contextweaver.adapters` to obtain implementation logic.

When core needs a protocol-specific pure transform (for example MCP result →
`ResultEnvelope` shaping), place that transform in an appropriate neutral/core
module and let the adapter re-export or call it for compatibility. Lazy imports
are not an acceptable way to hide a `core ↔ adapters` dependency cycle.

**Enforcement:** currently **review policy** pending the import-linter work in
#648. Changes fixing #752 should add a focused regression check where practical.

### Sensitivity defaults

The default sensitivity floor (`confidential`) and action (`drop`) are
conservative. Do not weaken those defaults without explicit security review.

**Enforcement:** configuration/sensitivity tests exercise the defaults; changing
the security posture still requires **review policy**.

### Data-layer purity

`types.py`, `envelope.py`, `config.py`, `serde.py`, and `exceptions.py` are data
and serialization layers: no network/filesystem I/O and no runtime orchestration.

**Enforcement:** currently **review policy**.

### ChoiceCard schema hiding

`ChoiceCard` carries whether a schema exists but not the full input/output schema.
Full schemas are hydrated only at the appropriate boundary. This preserves the
bounded-context property of browse/routing surfaces.

**Enforcement:** gateway/routing ChoiceCard and hydration tests.

### Serialization design

<a name="serialization-design"></a>

`serde.py` contains shared primitives; class-specific `to_dict()` / `from_dict()`
methods retain class-specific serialization semantics. Do not replace the latter
with indiscriminate `dataclasses.asdict()`.

**Enforcement:** serialization round-trip/golden tests; exact placement remains
**review policy**.

### Store protocols remain structural seams

Do not collapse store protocols into the bundled concrete backends. Structural
protocols are what allow user-provided and remote/persistent implementations.

**Enforcement:** store conformance tests exercise bundled implementations;
preserving the abstraction boundary is **review policy**.

## Safe vs unsafe simplifications

| Change | Safe? | Why |
|---|---|---|
| Replace ContextManager mixins while preserving public methods/behavior | Usually safe | The public contract is the invariant; the mixin layout is private |
| Fold a helper back into its natural parent when cohesion improves and the module stays ≤500 | Usually safe | Avoids artificial fragmentation and duplicated hardening paths |
| Add a field to an existing dataclass | Usually safe | Follow `to_dict`/`from_dict`, compatibility, schema/API gates |
| Merge two semantically distinct pipeline stages | **Unsafe by default** | Can change ordering, auditability, and security behavior |
| Replace structural store protocols with concrete classes | **Unsafe** | Removes backend extensibility |
| Duplicate `_utils.py` similarity logic in a caller | **Unsafe** | Creates diverging implementations |
| Put full schemas on `ChoiceCard` | **Unsafe** | Regresses bounded context cost |
| Add `context -> adapters` lazy imports | **Unsafe** | Hides a layer cycle rather than fixing it |

## Cross-cutting review policy

- `from __future__ import annotations` in source files unless a supported Python
  constraint gives a reviewed reason otherwise.
- Google-style docstrings on public classes/functions.
- Type hints on public functions/methods.
- Project exceptions from `contextweaver.exceptions` rather than ad-hoc public
  exception types.
- Reserved `metadata['_contextweaver']` namespace remains owned by the
  weaver-spec adapter contract; caller input must not be silently clobbered.

These are checked by combinations of lint/type/tests/review rather than one
single invariant gate; do not describe them as individually mechanically proved
unless a dedicated check is added.

## Update triggers

Update this file when:

- a new hard invariant gets a concrete enforcing test/gate;
- a previously mechanical invariant loses its enforcement;
- a review-policy constraint becomes mechanically enforced;
- a forbidden shortcut is discovered through a real failure;
- a safe/unsafe determination changes due to architectural evolution;
- the module-size policy or another cross-cutting architecture rule changes.

Every update must keep the stated enforcement status truthful. Documentation is
not evidence that an invariant is enforced.

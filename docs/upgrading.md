# Upgrading contextweaver

This page is the adopter-facing companion to the
[Stability and 1.0 Readiness](stability.md) page and the
[CHANGELOG](https://github.com/dgenio/contextweaver/blob/main/CHANGELOG.md).
Where the changelog records *what* changed in each release, this page states
the **versioning and deprecation policy** and gives **per-release notes for
changes that need user action**.

> The policy below documents the project's *current* pre-1.0 practice. It is
> deliberately modest and is subject to maintainer refinement before the 1.0
> promise is finalised.

## Versioning policy (0.x series)

contextweaver is pre-1.0 and marked **Alpha** in package metadata.

- **Minor releases (`0.MINOR.0`) may contain breaking changes** when correctness
  or clarity requires it. Each one is recorded in the `CHANGELOG.md` under its
  version, and any change that needs user action also gets an entry on this
  page.
- **Patch releases (`0.MINOR.PATCH`)** are reserved for fixes that do not change
  the public contract.
- **Experimental surfaces** (the `contextweaver mcp serve` gateway/proxy runtime
  and some adapters) may change faster than the core engines; the docs label
  them as experimental.
- **Internal modules** — anything whose name starts with `_` — may change at any
  time, including in patch releases.

## Deprecation policy

Deprecations are delivered through the runtime machinery in
`contextweaver._deprecation` (issue #517):

- A deprecated public surface emits a `DeprecationWarning` whose message starts
  with `contextweaver deprecation:` and names the replacement and the planned
  removal version.
- A surface is deprecated for **at least one minor release** before it is
  removed, and removals only happen in a release whose changelog calls them out.
- The set of active deprecations is tracked in one place
  (`contextweaver._deprecation.active_deprecations()`), which is the source for
  the inventory table below.

To see contextweaver's own deprecations as errors while testing your
integration (third-party `DeprecationWarning`s stay non-fatal):

```python
import warnings

warnings.filterwarnings("error", message="contextweaver deprecation:")
```

## Active deprecations

| Deprecated surface | Replacement | Deprecated in | Planned removal |
|---|---|---|---|
| `contextweaver.ToolCard` / `contextweaver.types.ToolCard` | `SelectableItem` | 0.16.0 | 1.0.0 |
| `RouteResult.debug_trace` | `RouteResult.trace` (structured `RouteTrace`) | 0.16.0 | 1.0.0 |
| `RouteTrace.to_legacy_dicts()` | the structured `RouteTrace` fields (`steps` / `to_dict()`) | 0.16.0 | 1.0.0 |
| `Router(scorer=...)` constructor argument | `Router(retriever=...)` (a `Retriever`) or `Router(scorer_backend=...)` | 0.16.0 | 1.0.0 |
| `ChoiceGraph.build_meta` (raw-dict accessor) | `ChoiceGraph.manifest` (typed `GraphManifest`) | documented (0.16.0) | not before 1.0.0 |
| Legacy `ArtifactRef` write path (empty `content_hash`, pre-#190) | firewall-written `ArtifactRef`s carry a populated `content_hash` | documented (0.16.0) | not before 1.0.0 |

The last two rows are **documentation-only** deprecations for now: `build_meta`
is still the on-disk serialization key the routing graph round-trips through
(so a runtime warning would fire on the library's own hot path), and the legacy
`ArtifactRef` shape is a data default rather than a call site there is a clean
place to warn from. They are recorded here so the 1.0 cleanup can retire them
deliberately; the maintainer may choose to attach runtime warnings once the
internal write paths are migrated.

### Migrating off the warned surfaces

```python
# ToolCard -> SelectableItem
from contextweaver import SelectableItem  # was: from contextweaver import ToolCard

# RouteResult.debug_trace -> RouteResult.trace
result = router.route("query")
steps = result.trace.steps          # was: result.debug_trace

# Router(scorer=...) -> retriever= / scorer_backend=
router = Router(graph, items=items, scorer_backend="bm25")  # was: scorer=BM25Scorer()
```

## Upgrade notes by release

Only releases that require user action are listed here. For the full change
history see the
[CHANGELOG](https://github.com/dgenio/contextweaver/blob/main/CHANGELOG.md).

### 0.16.0 (unreleased)

- **New deprecations.** The surfaces in the table above now emit
  `DeprecationWarning`s. Nothing is removed in this release — existing code
  keeps working — but migrate before 1.0.

### 0.15.0

- **Model-facing card text changed** for tool-rich items: a `destructive` /
  `read-only` safety marker is now reserved on `ChoiceCard` and can no longer be
  evicted by tag capping (issue #516). If you pin golden snapshots of rendered
  cards, regenerate them.

# Error Reference

Every error contextweaver raises inherits from `ContextWeaverError`
(in `contextweaver.exceptions`), so you can catch the whole family with one
`except` clause. Each class also carries:

- a stable, machine-readable **`code`** (e.g. `CW_CONFIG`) — branch on this
  instead of string-matching the message; it is safe to log, alert on, and pass
  across the gateway boundary or to non-Python clients, and
- an optional one-line **`hint`** — a remediation pointer, often a link back to
  the relevant section on this page.

Codes are part of the public compatibility surface: they are frozen against a
golden list in the test suite, so a rename or a missing code fails CI.

```python
from contextweaver.exceptions import ContextWeaverError

try:
    pack = manager.build_sync(phase, query)
except ContextWeaverError as exc:
    # exc.code is stable; exc.hint may point at the fix.
    logger.error("contextweaver failed: %s", exc, extra={"cw_code": exc.code})
    raise
```

`str(exc)` renders as `[<code>] <message> (hint: <hint>)` — for example
`[CW_CONFIG] unknown preset 'fast' (hint: check the configuration value or preset name; ...)`.
The message text itself is **not** a stable API; the code and any structured
attributes are.

## Code index

| Code | Exception | Raised when |
| --- | --- | --- |
| `CW_ERROR` | `ContextWeaverError` | Base class; not raised directly. |
| `CW_BUDGET_EXCEEDED` | `BudgetExceededError` | A build would exceed the configured token budget. |
| `CW_BUDGET_OVERFLOW` | `BudgetOverflowError` | Budget pressure dropped candidates under a fail-loud policy. |
| `CW_ARTIFACT_NOT_FOUND` | `ArtifactNotFoundError` | A requested artifact handle is absent from the store. |
| `CW_ARTIFACT_STORE_QUOTA` | `ArtifactStoreQuotaError` | A write would breach an artifact store's size/count quota. |
| `CW_POLICY_VIOLATION` | `PolicyViolationError` | An item violates the active `ContextPolicy`. |
| `CW_ITEM_NOT_FOUND` | `ItemNotFoundError` | A tool/agent/skill ID is not in the catalog or store. |
| `CW_GRAPH_BUILD` | `GraphBuildError` | The routing DAG cannot be constructed (e.g. a cycle). |
| `CW_ROUTE` | `RouteError` | The router cannot produce a valid route. |
| `CW_CATALOG` | `CatalogError` | An invalid catalog operation (duplicate IDs, schema). |
| `CW_CATALOG_VALIDATION` | `CatalogValidationError` | A catalog fails cross-item referential validation. |
| `CW_DUPLICATE_ITEM` | `DuplicateItemError` | A duplicate ID is appended to an append-only store. |
| `CW_CONFIG` | `ConfigError` | A configuration value or preset name is invalid. |
| `CW_VALIDATION` | `ValidationError` | A core data type fails construction-time validation. |
| `CW_DETERMINISM` | `DeterminismError` | A `deterministic=True` firewall path would invoke an LLM. |
| `CW_PATH_INVALID` | `PathInvalidError` | A `tool_browse` path violates the §3.2 grammar. |
| `CW_PATH_NOT_FOUND` | `PathNotFoundError` | A well-formed `tool_browse` path resolves to no node. |
| `CW_UPSTREAM` | `UpstreamError` | An upstream MCP tool call fails for transport/protocol reasons. |
| `CW_STORE_CLOSED` | `StoreClosedError` | An operation is attempted on a closed store. |
| `CW_UPSTREAM_STARTUP` | `UpstreamStartupError` | Live multi-upstream startup fails under the configured `StartupPolicy`. |

---

## ContextWeaverError

**Code:** `CW_ERROR`

The base of the hierarchy. It is not raised directly; catch it to handle every
contextweaver error in one place. Subclass it (not `Exception`) if you extend
the library so your error stays inside the family.

## BudgetExceededError

**Code:** `CW_BUDGET_EXCEEDED`

The public signal for a hard token-budget violation. The built-in fail-loud
path raises the more specific [`BudgetOverflowError`](#budgetoverflowerror)
(opt-in via `overflow_action="raise"`, issue #510), which attaches the would-be
`BuildStats`. Catch `BudgetExceededError` if you raise budget violations from
your own enforcement code.

**Fix:** raise the per-phase token budget, or trim the candidate set before the
build (see the [budget sizing guidance](troubleshooting.md)).

## BudgetOverflowError

**Code:** `CW_BUDGET_OVERFLOW`

Raised by `context/build_policy.py` when `ContextPolicy.overflow_action="raise"`
and budget pressure would drop candidates. Instead of silently shipping a
subtly-wrong prompt (e.g. a missing mandatory policy item), the build fails
loud. The would-be `BuildStats` is attached as `exc.stats`, and the distinct
dropped kinds as `exc.dropped_kinds`.

**Fix:** raise the phase token budget, relax the policy that marked the dropped
item mandatory, or set `overflow_action="drop"` to accept silent trimming.
Inspect `exc.stats` to see exactly what was kept and dropped.

## ArtifactNotFoundError

**Code:** `CW_ARTIFACT_NOT_FOUND`

Raised by the artifact-store backends (`store/artifacts.py`,
`store/json_file_artifacts.py`, `store/redis_artifacts.py`,
`store/s3_artifacts.py`) when a handle cannot be resolved.

**Fix:** verify the artifact ref came from the same store and has not expired or
been evicted; re-run the build that produced it if the store is ephemeral.

## ArtifactStoreQuotaError

**Code:** `CW_ARTIFACT_STORE_QUOTA`

Raised when a persistent `ArtifactStore` constructed with `max_bytes` /
`max_artifacts` limits (issue #497) would breach a limit on write.

**Fix:** raise the store's quota, prune old artifacts, or shorten artifact
lifetimes so long-running gateways stay within budget.

## PolicyViolationError

**Code:** `CW_POLICY_VIOLATION`

Raised during ingest (`context/ingest.py`) when an item violates the active
`ContextPolicy`.

**Fix:** adjust the item to satisfy the policy, or relax the policy if the
constraint is too strict for your workload.

## ItemNotFoundError

**Code:** `CW_ITEM_NOT_FOUND`

Raised when a requested tool/agent/skill ID is missing from the catalog
(`routing/catalog.py`), from a store (`store/*`), or from an external-memory
backend (`extras/memory/*`).

**Fix:** confirm the ID exists in the catalog/store and matches exactly
(IDs are case-sensitive); rebuild the catalog if it is stale.

## GraphBuildError

**Code:** `CW_GRAPH_BUILD`

Raised by `routing/tree.py`, `routing/graph.py`, and `routing/graph_io.py` when
the routing DAG cannot be built — for example a dependency cycle, a dangling
edge, or a missing root. Structured detail is attached so you can act without
parsing the message: `exc.cycle`, `exc.edge`, `exc.missing_root` (issue #523).

**Fix:** break the reported cycle, remove the dangling `depends_on`/`requires`
reference, or supply the missing root node.

## RouteError

**Code:** `CW_ROUTE`

Raised by `routing/router.py` and `routing/selection.py` when the router cannot
produce a valid route through the choice graph (e.g. no candidate survives the
beam search, or the graph has no reachable selectable items).

**Fix:** widen the routing budget/beam, check that the query matches indexed
items, and confirm the graph contains reachable selectable leaves.

## CatalogError

**Code:** `CW_CATALOG`

The base for catalog problems — duplicate IDs, schema violations, and invalid
catalog operations — raised across `routing/catalog.py`,
`routing/normalizer.py`, `routing/cards.py`, `routing/tool_id.py`, and the
protocol adapters under `adapters/` when they build catalogs from external
sources.

**Fix:** validate the catalog source for duplicate IDs and schema conformance
before loading; run `contextweaver catalog` validation on the file.

## CatalogValidationError

**Code:** `CW_CATALOG_VALIDATION`

A `CatalogError` subclass raised by the loaders' `on_invalid="raise"` path
(`routing/catalog.py`, issue #519) when cross-item referential validation fails.
The full `CatalogValidationReport` is attached as `exc.report` so you can
enumerate every dangling reference at once.

**Fix:** resolve the dangling `depends_on`/`requires` references listed in
`exc.report`, or load with `on_invalid="warn"` to triage incrementally.

## DuplicateItemError

**Code:** `CW_DUPLICATE_ITEM`

Raised when an item with an ID that already exists is appended to an append-only
store (`store/event_log.py`, `store/sqlite_event_log.py`,
`store/redis_event_log.py`, `context/_manager_ingest.py`).

**Fix:** use a unique ID per appended item, or check existence before appending
if duplicates are expected.

## ConfigError

**Code:** `CW_CONFIG`

Raised across the configuration surface (`config.py`, `profiles.py`,
`_scoring_config.py`, `routing/*`, `context/*`, adapters) when a configuration
value or preset name is invalid.

**Fix:** check the value or preset name against the documented options; the
message names the offending key.

## ValidationError

**Code:** `CW_VALIDATION`

Raised by the pure-data layer (`envelope.py`, `extras/llm_summarizer.py`) when a
core data type fails construction-time validation (issue #463). It also derives
from the builtin `ValueError`, so existing `except ValueError` call sites keep
working.

**Fix:** correct the field that failed validation; the message names the
constraint that was violated.

## DeterminismError

**Code:** `CW_DETERMINISM`

Raised by the context firewall (`context/firewall.py`, `context/ingest.py`) when
a `deterministic=True` path would have to invoke an LLM. Deterministic mode
*fails closed* (issue #404) so regulated callers can prove no data passed
through a summarisation model.

**Fix:** disable deterministic mode if model calls are acceptable, or supply a
deterministic (rule-based) summarizer/extractor so no LLM is needed.

## PathInvalidError

**Code:** `CW_PATH_INVALID`

A `CatalogError` subclass raised by `routing/path.py` when a `tool_browse` path
violates the §3.2 grammar.

**Fix:** correct the path syntax against the grammar in the
[gateway spec](gateway_spec.md).

## PathNotFoundError

**Code:** `CW_PATH_NOT_FOUND`

A `CatalogError` subclass raised by `routing/path.py` when a well-formed
`tool_browse` path resolves to no node.

**Fix:** browse from the root to discover valid paths; the catalog may have
changed since the path was constructed.

## UpstreamError

**Code:** `CW_UPSTREAM`

Signals an upstream MCP tool-call failure for transport/protocol reasons. Note
that the MCP gateway/proxy meta-tools never raise across the MCP boundary —
they return a structured [`GatewayError`](gateway_spec.md) payload with its own
wire codes (`UPSTREAM_TIMEOUT`, `AUTH_FAILED`, …) instead. Catch `UpstreamError`
when calling upstream helpers directly outside the meta-tool boundary.

**Fix:** check upstream connectivity/credentials; retry transient failures
(timeouts, unavailability) per the `retryable` hint on `GatewayError`.

## StoreClosedError

**Code:** `CW_STORE_CLOSED`

Raised by the SQLite-backed stores (`store/sqlite_facts.py`,
`store/sqlite_event_log.py`, `store/sqlite_episodic.py`) when an operation runs
after the backing connection was released via `close()`.

**Fix:** do not use a store after closing it; open a new instance, or use the
store as a context manager so its lifetime is scoped correctly.

## UpstreamStartupError

**Code:** `CW_UPSTREAM_STARTUP`

Raised by `adapters/upstream_launch.py` (`launch_upstreams`) when live
multi-upstream startup fails under the configured
`adapters.startup_policy.StartupPolicy` (issue #374): a `required` upstream
failed while `startup.mode: strict`, fewer than `min_healthy_upstreams`
upstreams started, or the effective catalog is empty and
`fail_on_empty_catalog` is set. The exception carries a `report` attribute
(a `StartupReport`) describing every upstream's individual startup outcome.

**Fix:** inspect `exc.report.statuses` for the per-upstream failure reason
(connection refused, auth failure, timeout, …); either fix the failing
upstream or relax `startup.mode` to `degraded` / lower
`min_healthy_upstreams` if partial startup is acceptable.

---

See also the [Troubleshooting guide](troubleshooting.md) for symptom-first
debugging and the [Stability page](stability.md) for the compatibility policy
that codes participate in.

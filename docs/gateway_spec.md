# Gateway Surface Specification

> **Status:** Active. This spec is the single source of truth for the
> proxy/gateway adapter surface. Implementation PRs (#13, #28, #29, #34)
> consume the decisions below; deviations require updating this document
> first.
>
> **Resolves:** [#30][i30] (stable `tool_id` format, ChoiceCard size bounds,
> path-navigation grammar), [#31][i31] (proxy schema-exposure strategy).
>
> **Informs:** [#13][i13] (transparent MCP proxy), [#28][i28] (two-tool
> gateway), [#29][i29] (shared `ProxyRuntime`), [#34][i34] (`tool_view`).

[i13]: https://github.com/dgenio/contextweaver/issues/13
[i28]: https://github.com/dgenio/contextweaver/issues/28
[i29]: https://github.com/dgenio/contextweaver/issues/29
[i30]: https://github.com/dgenio/contextweaver/issues/30
[i31]: https://github.com/dgenio/contextweaver/issues/31
[i34]: https://github.com/dgenio/contextweaver/issues/34

## Goals

- Lock the three contract gaps in [#30][i30] so independent implementations
  of [#13][i13] / [#28][i28] cannot diverge on the wire format.
- Commit the proxy and gateway to a single schema-exposure strategy
  ([#31][i31]) so [#29][i29] can build one `ProxyRuntime` core that serves
  both modes.
- Stay implementable on the surfaces already in the repository
  (`ChoiceCard`, `HydrationResult`, `Catalog.hydrate`,
  `mcp_tool_to_selectable`) — no new core types are required.

## Non-goals

- The proxy and gateway *runtimes* themselves — those land in [#13][i13],
  [#28][i28], [#29][i29].
- MCP wire-format details beyond what affects identifiers and card shape
  (transport, auth, streaming framing).
- Any persistence story for `tool_id`s. Adapters compute ids
  deterministically from upstream metadata; storage is implementation-defined
  per backend.

## 1. Stable `tool_id` format

### 1.1 Grammar

```
tool_id   = namespace ":" name [ "@" version ] [ "#" hash8 ]
namespace = [a-z] [a-z0-9_-]{0,63}
name      = [A-Za-z_] [A-Za-z0-9_.-]{0,127}
version   = [A-Za-z0-9._-]{1,32}
hash8     = [0-9a-f]{8}
```

Total `tool_id` length: ≤ 240 characters.

### 1.2 Field semantics

| Field | Source | Required | Stable across |
|---|---|---|---|
| `namespace` | Inferred from upstream tool name via `infer_namespace` (`adapters/mcp.py:24`) or upstream `server_name` if available. | Yes | Server restart, version bump within the same server identity. |
| `name` | Upstream tool name, derived per separator type (see §1.4). Dot/slash separators are stripped from `name` (the namespace field carries the prefix); underscore separators and the `mcp` fallback preserve the upstream value verbatim, because underscores can appear inside the upstream name itself and stripping would lose the original. | Yes | Description-only updates. |
| `version` | Upstream `_meta.version` or equivalent, if declared. | No | The lifetime of one upstream-declared version string. |
| `hash8` | First 8 hex chars of `sha256` over the canonical input-schema shape (see §1.3). | No, but **required** when `version` is absent. | Description-only updates; changes whenever input-schema shape changes. |

### 1.3 Hash input (canonical form)

When `version` is **absent**, adapters MUST emit `hash8`. The hash is
computed over two inputs concatenated: **(a)** the *upstream tool name*
(see §1.2 and the explanatory paragraph below) and **(b)** a
deterministic canonical form of the input-schema *shape* (top-level
property names + top-level `required` array — types and descriptions
are deliberately excluded so that prose-only edits do not churn the
id):

```python
import hashlib
import json

def _canonical_shape(input_schema: dict[str, object]) -> str:
    props = sorted((input_schema.get("properties") or {}).keys())
    required = sorted(input_schema.get("required") or [])
    return json.dumps(
        {"properties": props, "required": required},
        sort_keys=True,
        separators=(",", ":"),
    )

def _hash8(upstream_name: str, input_schema: dict[str, object]) -> str:
    canonical = upstream_name + "\n" + _canonical_shape(input_schema)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]
```

The `upstream_name` input is the original tool name as reported by the
upstream server, *before* any namespace stripping in §1.2. Including
the upstream name (not the derived `name` field of §1.2) ensures that
two upstream tools sharing an input-schema shape but originating in
different namespaces — for example `github.create_issue` and
`gitlab.create_issue`, both reduced to `name=create_issue` after
de-prefixing — still produce distinct `hash8` values.

`hashlib.sha256` is stdlib; no new core dependency is introduced.
[`rfc8785`](https://pypi.org/project/rfc8785/) (JSON Canonicalization
Scheme) is an acceptable substitute for `json.dumps(..., sort_keys=True)`
if a future ecosystem produces incompatible hashes, but is not required.

### 1.4 Examples

| Upstream tool | Declared version | `tool_id` |
|---|---|---|
| `github.create_issue` | `1.4.0` | `github:create_issue@1.4.0` |
| `slack_send_message` | *(none)* | `slack:slack_send_message#3a91c7d2` |
| `read_file` | *(none)* | `mcp:read_file#7b2f0e14` |
| `weather.get` | `2024-05` | `weather:get@2024-05` |

The four rows illustrate a single `name`-derivation rule keyed on the
separator `infer_namespace` matched:

- **Dot or slash** (`github.create_issue`, `weather.get`): the prefix
  used as `namespace` is dropped from `name` because the separator
  unambiguously splits the upstream string into two parts. The
  original is reconstructable as `f"{namespace}{sep}{name}"` once the
  adapter remembers which separator it matched.
- **Underscore with ≥ 3 segments** (`slack_send_message`): the prefix
  is **preserved** in `name`. Underscores can appear inside the
  upstream name itself, so stripping them would discard information
  the adapter needs to round-trip back to upstream.
- **`mcp` fallback** (`read_file`): no prefix was inferred, so `name`
  is the upstream value as-is.

This is the only `name`-derivation rule the spec defines; §1.2's row
is a short pointer to this section.

### 1.5 Collision behaviour

If two upstream tools resolve to the same `tool_id` after canonicalisation,
the adapter MUST raise `CatalogError` from
`contextweaver.exceptions`. Collisions in the 8-character hash space are
expected at ≈ 4 billion sibling tools per namespace; for the realistic
scale (≤ 10⁴ tools per server) the birthday-bound collision probability
is well below 1 in 10⁹. A collision in practice indicates either a
duplicate registration or a hashing bug — both of which deserve a loud
error, not silent disambiguation.

### 1.6 Round-trip

Implementations MUST treat `tool_id` as opaque except for the two
documented helpers:

- `parse_tool_id(s) -> ToolIdParts` — splits into `(namespace, name,
  version, hash8)`; raises `CatalogError` on malformed input.
- `format_tool_id(parts) -> str` — inverse of `parse_tool_id`.

The id MUST be the sole correlation key between `tool_browse` /
`tool_execute` / `tool_hydrate` and the underlying `SelectableItem`. Do
not key off the legacy `mcp:{name}` form.

### 1.7 Cutover from `mcp:{name}`

`mcp_tool_to_selectable` (`adapters/mcp.py:58`) currently emits
`id = f"mcp:{name}"`. The cutover is a single edit landing under
[#29][i29] and is not gated by a deprecation period. No backward
compatibility is preserved: test fixtures and example snippets that
hard-code `"mcp:<name>"` are rewritten in the same PR that flips the
adapter.

## 2. ChoiceCard size bounds

### 2.1 Permitted fields

A `ChoiceCard` (defined in `envelope.py:134`) carries **only** these
fields. No other field may be added to the rendered card without a spec
amendment:

- `id` — the canonical `tool_id` from §1.
- `name` — short display name (≤ 64 characters).
- `description` — single-line summary (see §2.3 for truncation).
- `tags` — sorted, deduplicated, **max 5 entries**, each ≤ 24 chars. The
  safety tags `destructive` / `read-only` are **exempt from the cap**: they
  are reserved into the kept set first so a safety marker can never be
  evicted by lexicographically earlier tags (issue #516).
- `kind` — one of `tool`, `agent`, `skill`, `internal`.
- `namespace` — copy of the `tool_id` namespace.
- `has_schema` — boolean. **Never** carries the schema itself.
- `score` — optional `float`. Omitted from prompt rendering; carried only
  in the JSON form.
- `cost_hint` — `float`. Rendered only when `> 0`.
- `side_effects` — `bool`. Rendered only when `true`.
- `safety` — one of `""`, `"read_only"`, `"destructive"` (issue #516). A
  first-class, **capping-immune** mirror of the read-only / destructive MCP
  annotation, derived from the item's safety tags (`destructive` wins over
  `read-only`). It guarantees the safety class survives even when the
  `tags` cap would drop the tag, and gives runtime policy layers (issue #373)
  a stable field to key on. Like the underlying annotation it is
  informational, **not** an authorization control (see the SECURITY NOTE in
  `adapters/mcp.py`).

### 2.2 Banned fields

A `ChoiceCard` MUST NOT carry, in any form (including stringified or
nested inside `tags`):

- `args_schema` / `inputSchema`
- `output_schema` / `outputSchema`
- Examples or sample invocations
- Free-form metadata maps (`annotations`, `_meta`, etc.)
- Server URLs or transport identifiers (those belong in
  `SelectableItem.metadata` on the *non-card* path)

This is the invariant that backs the `has_schema` flag's docstring in
`envelope.py:138` ("the schema itself is never included") and is the
load-bearing decision for the schema-exposure strategy in §4.

### 2.3 Token budgets

ChoiceCard rendering is bounded in **exact `tiktoken` token counts**
against the `cl100k_base` encoder (`tiktoken` is a core dependency per
`AGENTS.md` line 130). The bounds apply to the prompt-facing rendered
form, not the JSON serialisation.

| Bound | Target | Hard cap |
|---|---|---|
| Single card | ≤ 60 tokens | ≤ 80 tokens |
| `tool_browse` response (n cards) | ≤ `60 × n` tokens | ≤ `80 × n + 32` tokens (32-token preamble) |

Implementations MUST raise `CatalogError` if a single card exceeds the
hard cap after the truncation pass in §2.4. The card builder MUST NOT
silently drop fields to meet budget — truncation is confined to
`description`.

### 2.4 Description truncation

Truncation is deterministic, sentence-boundary-aware, and depends on no
external state:

1. Encode the candidate description with `cl100k_base`.
2. If the token count is within budget, emit verbatim.
3. Otherwise, find the longest prefix ending at a sentence terminator
   (`.`, `!`, `?`) that fits within budget.
4. If no sentence boundary fits, hard-cut at the byte offset whose token
   count is `budget − 1` and append the single character `…` (U+2026).
5. The truncated description MUST be stable for the same input — no
   randomness, no ML, no locale dependence.

### 2.5 Deterministic ordering

Within a `tool_browse` response, cards are ordered:

1. By descending `score` (highest first).
2. Ties broken lexicographically by `tool_id` ascending.

This matches the existing "tie-break by ID, sorted keys" invariant in
`docs/agent-context/invariants.md:42`.

### 2.6 Structured selection contract

The selection turn — where a downstream model picks one card from a browse
response — is the single point where model output re-enters the deterministic
pipeline. Two complementary, deterministic helpers bound it (no model calls):

- **Constrain before (issue #515).** `RouteResult.selection_schema(...)` (and
  the free function `routing.selection.selection_schema`) renders the routed
  candidate IDs as a JSON Schema whose selection property is an `enum` of those
  IDs. Provider variants: `json_schema` (bare), `openai` (the `response_format`
  `json_schema` envelope), `anthropic` (a tool definition with the schema as
  `input_schema`). Passing the schema to a provider's constrained-output API
  prevents the model from inventing or misspelling a `tool_id` at generation
  time. Raises `RouteError` when there are no candidates.
- **Validate after (issue #479).** `RouteResult.validate_selection(selected_id)`
  (and `routing.selection.validate_selection`) checks a returned ID against the
  offered candidates and returns a typed `SelectionValidation` with status
  `accepted` / `repaired` / `rejected`. Repair is deterministic and tried in a
  fixed order against the whitespace-stripped value — exact, then unique
  case-insensitive match, then unique case-insensitive prefix. Ambiguous
  case-fold / prefix matches are **rejected, never guessed**, so the contract
  can never silently route to the wrong tool. `RouteResult.to_routing_decision`
  runs this validation, stores the resolved canonical ID, and records the
  outcome under `metadata["contextweaver"]["selection"]`.

## 3. Path-navigation grammar

### 3.1 `tool_browse` arguments

`tool_browse` accepts exactly one of:

- `query: str` — free-form natural-language query. Routed through
  `Router.route` (`routing/router.py`). Returns the top-`k` cards.
- `path: str` — hierarchical path through the `ChoiceGraph` (see §3.2).
  Returns the children at the addressed node.

Passing both fields, or neither, is an error
(`{"error": "ARGS_INVALID", ...}`).

### 3.2 Path syntax

```
path     = "/" [ segment ( "/" segment )* ]
segment  = ( [a-z0-9] [a-z0-9_-]{0,63} ) | "*"
```

- A bare `/` lists root-level cards (one per namespace).
- A trailing `/` is invalid.
- Empty segments are invalid (`//foo`).
- The segment `*` is reserved for "list all children at this level". It
  is currently equivalent to omitting the trailing segment but is
  reserved so future filtered variants (e.g., `*?tag=read-only`) do not
  break clients that already special-case it.
- Segments are case-sensitive lowercase. Adapters are responsible for
  lower-casing namespaces during graph construction.
- Root-level segments resolve to namespaces and additionally satisfy
  §1.1's namespace grammar (must begin with `[a-z]`, not `[0-9]`).
  Deeper segments (clusters, tool-name leaves) may start with a digit
  when permitted by the addressed node.

### 3.3 Examples

| Path | Resolves to |
|---|---|
| `/` | Root nodes (one per namespace present in the catalog). |
| `/github` | Children of the `github` namespace node. |
| `/github/issues` | Children of `/github/issues` cluster. |
| `/github/issues/*` | Same as `/github/issues`; explicit "all" form. |
| `/github/issues/create_issue` | Leaf — returns a single card (or 404). |

### 3.4 Errors

Errors from `tool_browse` MUST use this shape (carried in the MCP tool
response `content` for the proxy, or as a structured `ResultEnvelope` for
the gateway):

```json
{
  "error": "PATH_INVALID" | "PATH_NOT_FOUND" | "ARGS_INVALID" | "SCHEMA_INVALID"
         | "UPSTREAM_ERROR" | "UPSTREAM_TIMEOUT" | "UPSTREAM_UNAVAILABLE"
         | "AUTH_FAILED" | "PERMISSION_DENIED" | "RATE_LIMITED"
         | "HYDRATE_FAILED" | "VIEW_FAILED",
  "message": "<human-readable, redacted>",
  "path": "<offending path or empty>",
  "retryable": false,
  "details": { /* optional, implementation-defined */ }
}
```

`PATH_INVALID` covers malformed paths (§3.2 violations); `PATH_NOT_FOUND`
covers well-formed paths that do not exist in the current `ChoiceGraph`.

**Upstream-error taxonomy (issue #485).** Failures from the wrapped MCP
server are classified so agents can branch without string-matching:
`UPSTREAM_TIMEOUT` and `UPSTREAM_UNAVAILABLE` (transient transport
failures), `AUTH_FAILED` / `PERMISSION_DENIED` (credential/authorization
problems), `RATE_LIMITED` (throttling), with `UPSTREAM_ERROR` as the
conservative fallback for everything else. The `retryable` boolean is a hint
that the same call may succeed on retry (set for timeouts, unavailability,
and rate limits). `SCHEMA_INVALID` flags an *upstream tool schema* that
failed ingest-time validation (§4.4), as opposed to `ARGS_INVALID` which
flags caller-supplied arguments.

**Detail redaction.** Upstream exception text can carry hostnames, paths, or
tokens. The `message` field is collapsed to a single control-character-free
line and length-capped before it reaches model-visible context; operators
retain the full, unredacted detail via server-side logging / event hooks.

## 4. Schema-exposure strategy

The proxy ([#13][i13]) and gateway ([#28][i28]) agree on one rule:
**`ChoiceCard` is the universal browse format, and schemas are only
materialised via hydration.** No `--full-schemas` opt-in is offered.

### 4.1 Transparent proxy (#13)

The transparent proxy publishes two surfaces:

1. **Discovery channel** — it intercepts the upstream `tools/list` and
   replaces each tool definition with a stripped form (so agents can
   see the full catalog but pay constant context cost per tool):

   ```jsonc
   {
     "name": "<canonical tool_id>",
     "description": "<truncated per §2.4>",
     "inputSchema": { "type": "object" }      // sentinel, no properties
   }
   ```

2. **Invocation channel** — it exposes the same two meta-tools the
   gateway exposes (§4.2): `tool_hydrate(tool_id)` for retrieving the
   real schema and `tool_execute(tool_id, args)` for invoking a tool.

The stripped entries in `tools/list` are **discovery-only**. Agents
MUST invoke tools through `tool_execute(tool_id, args)`; the proxy
looks up the upstream schema (via `Catalog.hydrate`), validates `args`,
calls upstream, and returns the result. Calling a stripped tool
directly by its name (= canonical `tool_id`) is not supported — and
the sentinel `inputSchema` guarantees any client that strictly
validates args against the declared schema would refuse to do so
anyway.

Agent flow:

```
tools/list                         → see stripped catalog (cards)
[optional] tool_hydrate(tool_id)   → retrieve real schema
tool_execute(tool_id, args)        → invoke upstream tool, get result
```

Why this is "transparent": the proxy surfaces the *entire* upstream
catalog (one card per tool) without filtering, ranking, or top-`k`
truncation. The gateway (§4.2) shares the same invocation channel
(`tool_hydrate` + `tool_execute`) but replaces the bulk discovery
channel with `tool_browse(query|path)` for query/path-scoped lookups.
Both modes converge on the same agent contract — only the discovery
channel differs.

A note on MCP `name` characters: the canonical `tool_id` may contain
`:` and `#`. MCP treats `name` as an opaque string, but a strict
downstream client MAY reject these characters. Proxy implementations
MAY URL-encode the `tool_id` (`%3A`, `%23`) in the `name` field when
they detect such a client; round-trip is preserved because §1.6's
`parse_tool_id` / `format_tool_id` consume the decoded form.

### 4.2 Two-tool gateway (#28)

The gateway exposes exactly three meta-tools (two from [#28][i28] plus
one from [#34][i34]):

- `tool_browse(query|path)` — returns `list[ChoiceCard]` per §2.
- `tool_execute(tool_id, args)` — looks up the upstream schema internally
  (no agent-visible hydrate step), validates `args`, calls upstream,
  pipes the response through the context firewall, and returns a
  `ResultEnvelope`. Any content persisted for later drilldown MUST appear in
  that envelope's `artifacts` list so the client can address it.
- `tool_view(handle, selector)` — drilldown into artifacts produced by a
  previous `tool_execute`. Specified in [#34][i34]; this document
  enumerates it only to fix the meta-tool surface count at three.

### 4.3 Hydration mechanism

Both modes route hydration through the existing
`Catalog.hydrate(item_id) -> HydrationResult` (`envelope.py:187`). No new
hydration primitive is introduced. The gateway calls it implicitly inside
`tool_execute`; the proxy exposes it through `tool_hydrate`. Cache
semantics are implementation-defined per backend but MUST honour the
`tool_id` as the sole cache key.

### 4.4 Validation

`tool_execute` (gateway) and the upstream call path (proxy) MUST validate
`args` against the hydrated schema before invoking upstream. Validation
errors return `{"error": "ARGS_INVALID", ...}` per §3.4 and MUST NOT
reach the upstream server.

**Schema trust and complexity (issue #484).** Upstream `inputSchema` /
`outputSchema` are themselves untrusted. At catalog ingest the runtime runs
JSON-Schema meta-validation (`check_schema`) and bounds schema complexity —
serialized size, nesting depth, and total property count, all configurable
via `SchemaLimits` with generous defaults. Findings are recorded on
`ProxyRuntime.last_refresh_report`; in the default lenient mode the tool is
still registered (flag-and-continue), while `on_invalid="raise"` rejects the
catalog. Compiled validators are cached per `tool_id` so repeated
`tool_execute` calls do not recompile. A schema that fails meta-validation
surfaces as `SCHEMA_INVALID` (not `ARGS_INVALID`) at execute time.

**Tolerant argument normalization (issue #488, opt-in).** With
`ProxyRuntime(tolerant_args=True)`, a deterministic, rule-based repair pass
runs *before* strict validation. The fixed rule set is:

| Rule | Condition | Action |
|---|---|---|
| `parse_stringified_object` | `args` is a string that parses as a JSON object | parse it (after stripping a leading BOM + surrounding whitespace) |
| `str_to_integer` | field schema type is `integer` and value is an exact integer literal | coerce to `int` |
| `str_to_number` | field schema type is `number` and value is a finite numeric literal | coerce to `float` |
| `str_to_boolean` | field schema type is `boolean` and value is exactly `"true"`/`"false"` | coerce to `bool` |
| `str_to_null` | field schema type is `null` (and not also `string`) and value is `"null"` | coerce to `None` |

No key renaming, no fuzzy matching, no dropping of unknown keys; a coercion
is applied only when the target schema type demands it (so a `string`-typed
field is never coerced). Anything not repaired still fails `ARGS_INVALID`.
Off by default, behaviour is byte-identical to strict validation. Every
applied repair is recorded under the result envelope's
`provenance["arg_repairs"]` so the normalization is auditable.

### 4.5 Dispatch-path controls (opt-in)

Four deterministic controls layer onto the `tool_execute` dispatch path (and,
where noted, `tool_browse` / `tool_view`). All are **off by default** — an
unconfigured runtime behaves exactly as specified above. They are configured on
`ProxyRuntime` or via the `mcp serve --config` blocks named below, applied in
this order: quota → dry-run → cache → dispatch-with-retry → cache-store.

**Retry/backoff (issue #529, config `retry`).** With a `RetryPolicy`
(`max_attempts`, `base_delay`, `max_delay`, `jitter`, `retryable_codes`),
transient upstream failures are retried with bounded exponential backoff.
Only exceptions classified retryable (transport-level `UPSTREAM_TIMEOUT` /
`UPSTREAM_UNAVAILABLE` by default) are retried; tool-level error *results*
(`isError=true`) and non-retryable codes dispatch exactly once. The default
`max_attempts=1` is byte-identical to the single-attempt behaviour. Attempt
counts appear in the `execute.completed` diagnostic.

**Read-only response cache (issue #512, config `cache`).** With a
`ToolResultCache`, two identical `tool_execute` calls (same `tool_id` and
argument-order-insensitive args) for an upstream-declared **read-only** tool
dispatch upstream only once. Caching is operator opt-in (a `cache` block with
`read_only: true`, plus an optional `allow` list of `tool_id`s); mutating tools
and error results are never cached; entries are TTL- and size-bounded (LRU) and
invalidated wholesale on catalog refresh. A cache hit is marked
`provenance["cache_hit"]=true`. Annotations are upstream-controlled, so caching
is never inferred — it requires explicit operator opt-in.

Read-only eligibility is derived from the upstream `readOnlyHint`, an
**unverified** server-declared hint (see the SECURITY NOTE in
`adapters/mcp.py` — these hints must not drive safety-critical decisions).
Enabling `cache.read_only: true` with no `allow` list therefore trusts every
upstream's self-declaration: a mutating tool that falsely declares itself
read-only would have its first result cached and a second identical call served
from cache **without** re-dispatching the side effect. Pair `read_only: true`
with an explicit `allow` list of `tool_id`s for safety-critical deployments
rather than trusting hints globally.

**Dry run (issue #483).** `tool_execute` accepts an optional `dry_run: bool`.
When set, the runtime performs hydration, argument validation, and quota
evaluation, then returns a report **without** invoking upstream or writing
artifacts:

```json
{
  "dry_run": true,
  "tool_id": "billing:refund@1#a1b2c3d4",
  "upstream_name": "refund",
  "args_valid": true,
  "annotations": {"destructiveHint": true, "verified": false},
  "checks": [{"name": "schema_validation", "status": "pass"},
             {"name": "rate_limit", "status": "pass"}]
}
```

Invalid arguments still return the same `ARGS_INVALID` diagnostics as a real
call. Declared annotations are echoed but always stamped `verified=false` (they
are unverified upstream hints, per §4.4). A dry run never consumes rate-limit
quota and is recorded as a distinct `execute.dry_run` diagnostic.

**Rate limiting / quotas (issue #482, config `rate_limits`).** With a
`RateLimiter`, per-session invocation limits are enforced per meta-tool
(`tool_browse` / `tool_execute` / `tool_view`) and per `tool_id`, each with an
optional sliding 60-second `max_calls_per_minute` and a cumulative
`max_calls_per_session`. A breach returns `{"error": "RATE_LIMITED", "retryable":
true, "details": {"scope": ..., "retry_after": ...}}` per §3.4 and does **not**
dispatch upstream. Limits are per process; in stdio deployments (one client per
process) per-session is effectively per-process. Unconfigured deployments are
unaffected.

### 4.6 Catalog-refresh consistency contract

`tool_execute` resolves the model-selected canonical `tool_id` to a raw upstream
name through an internal index before dispatch. To rule out dispatching an
execution to the wrong upstream tool after a catalog change, the runtime
guarantees:

- **Atomic rebuild.** `refresh_catalog` / `register_tool_defs_sync` rebuild every
  catalog-derived structure — the canonical-id→upstream-name index, the
  per-`tool_id` compiled-validator cache, the read-only response cache, the
  cache-stable browse state, the `ChoiceGraph`, and the `Router` — within a
  single synchronous call. No `await` occurs mid-rebuild, so a concurrent
  `execute` observes either the fully-old or fully-new view, never a half-updated
  one.
- **Renamed/removed tools fail closed.** Executing a `tool_id` that no longer
  exists after a refresh returns a clean `HYDRATE_FAILED`; it is never silently
  dispatched to an unrelated upstream tool.
- **Cross-upstream duplicate names collapse deterministically.** `MultiplexUpstream`
  de-duplicates duplicate raw tool names at `tools/list` (first source wins), so
  the catalog never holds an ambiguous canonical-id→upstream-name mapping.

These guarantees are pinned by the characterization tests in
`tests/test_proxy_runtime.py` (refresh-rename, refresh-removal, duplicate-raw-name).

## 5. Cache-stable tool browsing (`cache_stable=True`)

The default §2.5 ordering (score desc, id asc) maximises agent-side
ranking quality. When a downstream client uses prompt caching, that
default sacrifices cache hits: a later browse with a different query
re-ranks the same items, producing different prompt bytes for the same
ids and invalidating any prefix-cached state on the model side.

`ProxyRuntime(cache_stable=True)` opts into a **byte-stable prefix
ordering** for repeated browses in the same runtime/session.

### 5.1 Behaviour

When `cache_stable=True`:

1. The runtime tracks the set of `tool_id`s that have been **browsed**
   or **hydrated** during the session.
2. On every `tool_browse(query|path)` call:
   - Cards whose id is already in the session's seen-set are emitted
     **first**, sorted **ascending by `id`**.
   - A single internal `ChoiceCard` with id
     `__cache_breakpoint__` and `kind="internal"` is emitted **only if
     both the seen-prefix and the new-suffix are non-empty**. The
     marker is the explicit boundary downstream serialisers should
     insert their cache breakpoint at (e.g. Anthropic
     `cache_control: {"type": "ephemeral"}` or OpenAI prompt-cache
     keys).
   - Newly-discovered cards are emitted **after** the marker, also
     sorted ascending by `id`.
3. The content of every card in the seen-prefix is **frozen on first
   sighting**. Subsequent browses re-emit the cached content even if
   the router would have produced a different `score` value for the
   same item under a different query. This is what makes the prefix
   byte-stable.

### 5.2 Ranking metadata is preserved

`ChoiceCard.score` is preserved on every card. **Important caveat:**
when `cache_stable=True`, the first emitted card is not necessarily
the highest-ranked card — ranking must be read from
`ChoiceCard.score`, not inferred from position. Consumers that want
to display tools in rank order can sort by `score` after receiving
the response; the cache-stable ordering is for the wire / prompt
representation, not the UI.

### 5.3 Marker is suppressed when there is no boundary

- First browse in a session → all cards are new → **no marker**.
- A browse whose ids are entirely already-seen → **no marker** (the
  whole response is the stable prefix).
- A browse whose ids are entirely new → **no marker**.

### 5.4 MCP compliance

The marker is a real `ChoiceCard` (`kind="internal"`); it satisfies
every existing serialisation that already handles `internal` cards
(path-browse cluster nodes, §3.3 Examples). MCP-format tool-list
output is unaffected because the marker only appears in
`tool_browse` response payloads, not in `tools/list` (§4.1).

### 5.5 Failed hydrations are not recorded

`hydrate(tool_id)` only enters the seen-set when it succeeds.
`GatewayError(code="HYDRATE_FAILED")` is a no-op for the cache state.

## 6. Implementation dependencies (recommended)

These are **suggestions** for [#13][i13] / [#28][i28] / [#29][i29] /
[#34][i34], not requirements of this spec. Each goes under
`[project.optional-dependencies]` per the existing pattern in
`pyproject.toml`, not into core. The AGENTS.md "heavy or runtime-specific
packages live under optional-dependencies" guideline still applies.

| Package | Extras group | Purpose |
|---|---|---|
| [`mcp`](https://pypi.org/project/mcp/) | `[mcp]` | Official Python SDK; saves re-implementing JSON-RPC framing for the transparent proxy. |
| [`jsonschema`](https://pypi.org/project/jsonschema/) | `[gateway]` | Schema validation for `tool_execute` args (§4.4). |
| [`rfc8785`](https://pypi.org/project/rfc8785/) | *(optional)* | Canonical JSON if §1.3's `json.dumps(sort_keys=True)` proves insufficient for cross-implementation hash agreement. Not currently needed. |

Core (`pyproject.toml.dependencies`) does **not** change as part of this
spec. The runtime PRs decide for themselves whether to take these
extras.

## 7. Invariants captured by this spec

The two assertions below are added to
[`docs/agent-context/invariants.md`](https://github.com/dgenio/contextweaver/blob/main/docs/agent-context/invariants.md) when
this spec lands and are review blockers for any future PR touching the
adapter or routing surfaces:

- **`ChoiceCard` never carries `args_schema` or `output_schema`** — the
  `has_schema` flag is the only schema-related field permitted. Violating
  this regresses the constant-context-cost property of the gateway
  surface.
- **`tool_id` produced by adapters MUST round-trip through
  `parse_tool_id` / `format_tool_id`** — once those helpers land in
  [#29][i29], string-formatted ids elsewhere in the codebase are a
  review blocker.

## 8. References

- [`envelope.py:134`](https://github.com/dgenio/contextweaver/blob/main/src/contextweaver/envelope.py#L134)
  — `ChoiceCard` definition (the schema-free fact that §2 codifies).
- [`adapters/mcp.py:58`](https://github.com/dgenio/contextweaver/blob/main/src/contextweaver/adapters/mcp.py#L58)
  — `mcp_tool_to_selectable`; emits the legacy `mcp:{name}` form §1.7
  retires.
- [`adapters/mcp.py:24`](https://github.com/dgenio/contextweaver/blob/main/src/contextweaver/adapters/mcp.py#L24)
  — `infer_namespace`; the canonical source of the `namespace` field in
  §1.1.
- `routing/catalog.py` — `Catalog.hydrate`, the hydration primitive both
  §4.1 and §4.2 reuse.
- [`docs/agent-context/invariants.md`](https://github.com/dgenio/contextweaver/blob/main/docs/agent-context/invariants.md) —
  destination of the assertions in §6.

## 9. Cross-primitive identity and collision policy (resources & prompts)

MCP exposes three first-class context primitives — **tools**, **resources**,
and **prompts**. The gateway shapes all three with the same bounded-choice +
firewall treatment (#555). To route them through one shared
[`Catalog`](https://github.com/dgenio/contextweaver/blob/main/src/contextweaver/routing/catalog.py),
they need one identity scheme that cannot collide across kinds — a tool named
`search` and a prompt named `search` must remain distinct items.
[`routing/primitive_id.py`](https://github.com/dgenio/contextweaver/blob/main/src/contextweaver/routing/primitive_id.py)
is the single source of truth for this policy (#671).

### 9.1 Grammar

```
primitive_id = [ kind "::" ] tool_id
kind         = "resource" | "prompt"     ; "tool" is implied and never written
```

- **Tools** keep the bare §1 form (`namespace:name[@version][#hash8]`) — the §1
  grammar, fixtures, and existing catalogs are unchanged.
- **Resources** and **prompts** prepend a reserved `kind` tag with the `::`
  separator: `resource::fs:readme#ab12cd34`, `prompt::gh:summarize#deadbeef`.
  `::` cannot appear in a tool id (§1.1 uses a single `:` and bans `:` inside
  the namespace/name), so the three id spaces are **disjoint by construction**.
- The body after `kind::` obeys the §1.1 grammar exactly and is validated
  through the shared `parse_tool_id` / `format_tool_id` helpers, so both
  directions agree.

### 9.2 Shape hashes

`hash8` keeps an id stable across prose-only edits (§1.3). Each primitive
hashes over its identity-defining shape, with a domain prefix so the three
hash spaces never alias:

- **Tool** — `upstream_name` + canonical input-schema shape (§1.3, unchanged).
- **Resource** — the canonical `uri` (the resource's stable MCP identity).
- **Prompt** — the prompt name + its **sorted** argument names (the argument
  *set* defines the call shape; descriptions and rendered messages do not).

### 9.3 Collision handling

When two **distinct** primitives of the same kind still map to the same
canonical id, `resolve_collisions` assigns a deterministic `~N` suffix: the
lowest catalog index keeps the bare id, and later occurrences (in ascending
index order) become `id~2`, `id~3`, …. The result is independent of input
ordering, preserving the project's determinism invariant. Identical ids that
refer to the *same* primitive are de-duplicated by the caller first.

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
| `version` | Upstream `_meta.version` or equivalent, if declared. | No | The lifetime of one upstream-declared semver. |
| `hash8` | First 8 hex chars of `sha256` over the canonical input-schema shape (see §1.3). | No, but **required** when `version` is absent. | Description-only updates; changes whenever input-schema shape changes. |

### 1.3 Hash input (canonical form)

When `version` is **absent**, adapters MUST emit `hash8`. The hash is
computed over a deterministic canonical form of the input-schema *shape*
(top-level property names + top-level `required` array — types and
descriptions are deliberately excluded so that prose-only edits do not
churn the id):

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

def _hash8(name: str, input_schema: dict[str, object]) -> str:
    canonical = name + "\n" + _canonical_shape(input_schema)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]
```

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
- `tags` — sorted, deduplicated, **max 5 entries**, each ≤ 24 chars.
- `kind` — one of `tool`, `agent`, `skill`, `internal`.
- `namespace` — copy of the `tool_id` namespace.
- `has_schema` — boolean. **Never** carries the schema itself.
- `score` — optional `float`. Omitted from prompt rendering; carried only
  in the JSON form.
- `cost_hint` — `float`. Rendered only when `> 0`.
- `side_effects` — `bool`. Rendered only when `true`.

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
  "error": "PATH_INVALID" | "PATH_NOT_FOUND" | "ARGS_INVALID",
  "message": "<human-readable>",
  "path": "<offending path or empty>",
  "details": { /* optional, implementation-defined */ }
}
```

`PATH_INVALID` covers malformed paths (§3.2 violations); `PATH_NOT_FOUND`
covers well-formed paths that do not exist in the current `ChoiceGraph`.

## 4. Schema-exposure strategy

The proxy ([#13][i13]) and gateway ([#28][i28]) agree on one rule:
**`ChoiceCard` is the universal browse format, and schemas are only
materialised via hydration.** No `--full-schemas` opt-in is offered.

### 4.1 Transparent proxy (#13)

The transparent proxy intercepts the upstream `tools/list` and replaces
each tool definition with a stripped form:

```jsonc
{
  "name": "<canonical tool_id>",
  "description": "<truncated per §2.4>",
  "inputSchema": { "type": "object" }      // sentinel, no properties
}
```

Agents that need the real schema MUST call the meta-tool
`tool_hydrate(tool_id)`, which returns the upstream schema verbatim. The
proxy then forwards `tool_execute(tool_id, args)` to the upstream MCP
server.

Why this is acceptable for "transparent" proxies: even today,
`Catalog.hydrate` (`routing/catalog.py`) already supplies the schema on
demand; the proxy is exposing that same capability over MCP. Agent
runtimes that cannot tolerate a hydrate step are a known limitation —
they should use the gateway mode (§4.2) instead.

### 4.2 Two-tool gateway (#28)

The gateway exposes exactly three meta-tools (two from [#28][i28] plus
one from [#34][i34]):

- `tool_browse(query|path)` — returns `list[ChoiceCard]` per §2.
- `tool_execute(tool_id, args)` — looks up the upstream schema internally
  (no agent-visible hydrate step), validates `args`, calls upstream,
  pipes the response through the context firewall, and returns a
  `ResultEnvelope`.
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

## 5. Implementation dependencies (recommended)

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

## 6. Invariants captured by this spec

The two assertions below are added to
[`docs/agent-context/invariants.md`](agent-context/invariants.md) when
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

## 7. References

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
- [`docs/agent-context/invariants.md`](agent-context/invariants.md) —
  destination of the assertions in §6.

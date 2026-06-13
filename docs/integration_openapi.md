# OpenAPI integration

`contextweaver.adapters.openapi` converts an [OpenAPI](https://spec.openapis.org/)
document (3.0 or 3.1) into a routing catalog: every operation becomes a
`SelectableItem`. Teams whose "tools" are REST APIs get bounded-choice routing
without hand-building a catalog — and a real-world spec with hundreds of
operations is exactly the context-rot case this library exists for.

> **contextweaver routes; it does not call.** The adapter never makes an HTTP
> request. The selected operation's `metadata` carries the method and path so
> your own HTTP client can dispatch it.

## Loading a spec

```python
from contextweaver.adapters.openapi import load_openapi_catalog
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder

catalog = load_openapi_catalog("openapi.yaml")   # dict, JSON path, or YAML path
items = catalog.all()
router = Router(TreeBuilder().build(items), items=items, beam_width=5)

result = router.route("remove a pet from the store")
op = catalog.get(result.candidate_ids[0])
print(op.metadata["http_method"], op.metadata["http_path"])   # e.g. DELETE /pets/{petId}
```

## Mapping

| OpenAPI | `SelectableItem` |
|---|---|
| `operationId` (fallback: `{method}_{path-slug}`) | `id` (`openapi:{operationId}`) and `name` |
| `summary` / `description` (fallback: `{METHOD} {path}`) | `description` |
| `tags` + method-derived safety tags | `tags` |
| `parameters` + `requestBody` | `args_schema` (see composition below) |
| method, path, `operationId`, `deprecated`, `servers`, `security` | `metadata` |

### Safety tags (mirrors the MCP adapter)

- `GET` / `HEAD` → `read-only` tag, `side_effects = False`.
- `OPTIONS` / `TRACE` → `side_effects = False` (no extra tag).
- `POST` / `PUT` / `PATCH` → `side_effects = True`.
- `DELETE` → `destructive` tag, `side_effects = True`.

### `args_schema` composition rule

Parameters become top-level properties keyed by name (the parameter
`description` is folded into the property schema; `required` parameters join
`required`). The `application/json` request body is resolved and, when it is an
object schema whose property names do not collide with parameters, its
properties are merged in; otherwise the whole body schema is nested under a
`body` property.

### `servers` resolution

`metadata["servers"]` follows the OpenAPI override order: an operation's own
`servers` wins, then the path-item `servers`, then the document-level
`servers`. The value is preserved for reference only — the adapter routes and
never dispatches the call.

## `$ref` and version support

- **Local refs** (`#/components/...`) are fully resolved, including recursive
  schemas (a cycle truncates to `{}`).
- **External refs** (a file or URL) raise `CatalogError` — the adapter never
  fetches the network. Bundle external schemas into a single local-ref document
  first.
- OpenAPI 3.0 and 3.1 are both supported for the operation-listing + schema
  subset; vendor extensions and execution-time constructs (auth flows, servers)
  are preserved in `metadata` but not interpreted.

A runnable, network-free example lives at
[`examples/openapi_routing_demo.py`](https://github.com/dgenio/contextweaver/blob/main/examples/openapi_routing_demo.py).

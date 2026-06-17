# HTTP sidecar client examples

Language-agnostic clients for the contextweaver HTTP sidecar (issue #427). The
sidecar exposes the router and context firewall over a small, versioned
HTTP/JSON API so non-Python agents can use them without embedding Python.

Start a server (route + compact):

```bash
contextweaver serve-api --catalog examples/sample_catalog.json --port 8731
```

Or compact-only (no catalog needed):

```bash
contextweaver serve-api --port 8731
```

## Endpoints

| Method & path     | Purpose                          | Schema |
|-------------------|----------------------------------|--------|
| `GET  /v1/health` | Liveness probe (unauthenticated) | —      |
| `POST /v1/route`  | Tool routing                     | `schemas/sidecar/v1/route_request.schema.json` → `route_response.schema.json` |
| `POST /v1/compact`| Tool-result compaction           | `schemas/sidecar/v1/compact_request.schema.json` → `compact_response.schema.json` |

Errors use the shape in `schemas/sidecar/v1/error.schema.json`.

## Python

`../sidecar_demo.py` is a self-contained Python client that also starts a server
in-process (it runs under `make example`). Against a running server, a client is
just `urllib`/`requests` POSTing JSON to the endpoints above.

## TypeScript

`client.ts` is a dependency-free client using the global `fetch` (Node >= 18,
Deno, Bun, browsers). Types mirror the published v1 JSON Schemas.

```bash
SIDECAR_URL=http://127.0.0.1:8731 npx tsx examples/sidecar/client.ts
```

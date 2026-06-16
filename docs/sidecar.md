# HTTP sidecar (language-agnostic route/compact API)

contextweaver's engines are Python, but much of the agent ecosystem is not. The
**HTTP sidecar** (`contextweaver serve-api`) exposes the two highest-value
primitives over a small, versioned HTTP/JSON API so any language can use the
deterministic router and the context firewall without embedding Python:

- `POST /v1/route` — tool routing over a catalog.
- `POST /v1/compact` — single-call tool-result compaction (the context firewall).
- `GET /v1/health` — unauthenticated liveness probe.

It is built on the Python standard library (`http.server`) — **no extra
dependency** — and reuses the same sync [`Router`](concepts.md) and
[`compact_tool_result`](concepts.md) facade the in-process API uses, so wire
results match what Python callers get.

## Starting the server

Route + compact (needs a catalog):

```bash
contextweaver serve-api --catalog examples/sample_catalog.json --port 8731
```

Compact-only (no catalog required):

```bash
contextweaver serve-api --port 8731
```

Useful flags:

| Flag | Default | Meaning |
|---|---|---|
| `--catalog PATH` | _(none)_ | Tool catalog JSON. Omit to disable `/v1/route`. |
| `--host` / `--port` | `127.0.0.1` / `8731` | Bind address. |
| `--top-k` | `50` | Routing **ceiling**; per-request `top_k` caps below it. |
| `--api-key` | _(none)_ | Require `Authorization: Bearer <key>` on route/compact (env: `CONTEXTWEAVER_SIDECAR_API_KEY`). |
| `--rate-per-minute` | _(none)_ | Per-client sliding-window request cap. |
| `--max-body-bytes` | `1048576` | Reject larger request bodies. |

## Contract

Request/response/error shapes are published as JSON Schemas under
`schemas/sidecar/v1/` (with example payloads under `schemas/sidecar/v1/examples/`).
The `api_version` field on every response echoes the path prefix (`v1`).

### `POST /v1/route`

```json
{ "query": "send a follow-up email", "top_k": 5, "allowed_namespaces": ["email"] }
```

→

```json
{
  "api_version": "v1",
  "candidate_ids": ["email.send"],
  "scores": [0.82],
  "is_ambiguous": false,
  "clarifying_question": null,
  "cards": [{ "id": "email.send", "name": "send", "description": "Send an email" }]
}
```

### `POST /v1/compact`

```json
{ "data": { "rows": ["...large blob..."] }, "threshold_chars": 2000 }
```

→

```json
{
  "api_version": "v1",
  "firewalled": true,
  "payload": { "_cw_summary": "…", "_cw_artifact_ref": "artifact:…", "_cw": {} },
  "summary": "…",
  "facts": [],
  "artifact_ref": "artifact:compact:…",
  "tokens_saved": 1820
}
```

### Errors

Every error uses the same shape (mirroring the gateway error contract,
`gateway_spec.md` §3.4):

```json
{ "error": "RATE_LIMITED", "message": "rate limit exceeded", "retryable": true }
```

| HTTP | `error` | When |
|---|---|---|
| 400 | `BAD_REQUEST` | malformed body or invalid field |
| 401 | `UNAUTHORIZED` | missing/invalid bearer token (when `--api-key` is set) |
| 404 | `NOT_FOUND` | unknown path |
| 405 | `METHOD_NOT_ALLOWED` | wrong method for the path |
| 413 | `PAYLOAD_TOO_LARGE` | body exceeds `--max-body-bytes` |
| 429 | `RATE_LIMITED` | per-client quota exceeded (`retryable: true`) |
| 503 | `ROUTING_UNAVAILABLE` | `/v1/route` called on a compact-only server |

## Clients

- **Python:** `examples/sidecar_demo.py` — self-contained (starts a server
  in-process and drives it over `urllib`).
- **TypeScript:** `examples/sidecar/client.ts` — dependency-free, uses global
  `fetch`; types mirror the v1 schemas.

See `examples/sidecar/README.md` for details.

## Security notes

- Auth is **off by default** for local use. Set `--api-key` (or the
  `CONTEXTWEAVER_SIDECAR_API_KEY` env var) before exposing the sidecar beyond
  loopback, and front it with TLS at your ingress — the stdlib server speaks
  plain HTTP.
- Rate limiting is per client (the bearer token when present, otherwise the
  source address) and uses the same sliding-window limiter as the MCP gateway.
- The body-size cap bounds memory blast radius from hostile payloads.

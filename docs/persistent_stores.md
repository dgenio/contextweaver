# Persistent & remote stores

contextweaver's store layer is protocol-based: the engine depends on the four
store protocols (`EventLog`, `ArtifactStore`, `EpisodicStore`, `FactStore`),
not on any concrete backend. This page covers the persistent and remote
backends and how to wire them into a long-running gateway.

All backends are validated by the same conformance kit
(`contextweaver.store.testing`), so they are drop-in interchangeable.

## Backend matrix

| Backend | Roles | Install | Use when |
|---|---|---|---|
| `InMemory*` | all four | core | tests, single-shot scripts |
| `SqliteEventLog` | event log | core (stdlib) | single-process durable history |
| `SqliteEpisodicStore` / `SqliteFactStore` | episodic / facts | core (stdlib) | durable local memory, no external service |
| `JsonFileArtifactStore` | artifacts | core (stdlib) | human-inspectable on-disk artifacts |
| `RedisEventLog` / `RedisArtifactStore` | event log / artifacts | `pip install 'contextweaver[redis]'` | multi-process / shared gateway state |
| `S3ArtifactStore` | artifacts | `pip install 'contextweaver[s3]'` | large/long-lived artifacts (AWS S3, MinIO, R2, GCS) |

## Local persistence: SQLite

The SQLite backends need no external service and share one database file — each
store type tracks its own schema migrations under a distinct version table.

```python
from contextweaver.store import SqliteEventLog, SqliteEpisodicStore, SqliteFactStore, StoreBundle

stores = StoreBundle(
    event_log=SqliteEventLog("agent.db"),
    episodic_store=SqliteEpisodicStore("agent.db"),  # same file, separate tables
    fact_store=SqliteFactStore("agent.db"),
)
```

Search and ordering semantics match the in-memory backends exactly, so swapping
backends never changes context-build output. They are single-process and
synchronous (the connection is thread-affine).

## Remote backends: Redis & S3

Redis and S3 backends import their client libraries lazily — importing
`contextweaver.store` never requires the extra. Pass a pre-built client or a
connection target:

```python
import redis
from contextweaver.store import RedisArtifactStore, RedisEventLog, S3ArtifactStore

client = redis.Redis.from_url("redis://localhost:6379/0")
artifacts = RedisArtifactStore(client=client, namespace="prod", ttl_seconds=86_400)
events = RedisEventLog(client=client, namespace="prod")

s3 = S3ArtifactStore("my-bucket", endpoint_url="https://minio.local", prefix="artifacts")
```

`RedisArtifactStore` supports an optional per-artifact TTL and namespace
isolation; `S3ArtifactStore` works with any S3-compatible endpoint.

## Async backends

For async network backends, wrap a thread-safe sync store as the async protocol
with `to_async(store)`, or pass an async store to `ContextManager` (via
`StoreBundle`) — the manager bridges it to the synchronous pipeline and keeps
the event loop responsive during `await build(...)`. See
`contextweaver.store.async_protocols`.

## Persisting gateway state across restarts

`contextweaver mcp serve --state-dir DIR` wires the gateway's per-session
`ContextManager` to file-backed stores so artifact handles and event history
survive a restart:

```bash
contextweaver mcp serve --gateway --catalog catalog.json --state-dir ./gateway-state
```

This lays out `gateway-state/events.sqlite3` (a `SqliteEventLog`) and
`gateway-state/artifacts/` (a `JsonFileArtifactStore`). Restarting the server
against the same `--state-dir` rehydrates prior events and keeps previously
issued artifact handles resolvable via `tool_view`. The same key works in a
config file:

```yaml
# gateway.yaml
catalog: catalog.json
mode: gateway
state_dir: ./gateway-state
```

Without `--state-dir`, the gateway uses in-memory stores (the zero-config
default) and state is lost on exit.

!!! note "Data retention"
    Persisted artifacts are the raw, firewalled upstream payloads written to
    disk. Treat the state directory with the same care as any store of
    tool-result data, and prefer a backend whose durability and access controls
    match your deployment.

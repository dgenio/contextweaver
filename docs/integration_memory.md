# External Memory Backends

> Wire an existing [Mem0](https://docs.mem0.ai/) / [Zep](https://www.getzep.com/)
> / [LangMem](https://langchain-ai.github.io/langmem/) deployment as the
> backing store for contextweaver's optional long-lived stores
> (`EpisodicStore`, `FactStore`) so contextweaver compiles per-turn
> prompts on top of memory you've already invested in — instead of
> forcing a separate in-memory or SQLite store.

## Why

contextweaver and external memory services solve **complementary**
problems:

- **External memory layers** (Mem0, Zep, LangMem) hold cross-session
  state: passive memory extraction, temporal knowledge graphs,
  semantic / episodic / procedural memory.
- **contextweaver** compiles those memories — plus the current turn's
  tool calls and tool results — into a phase-specific, budget-aware
  prompt every time the LLM is invoked.

The two compose: external memory persists across sessions;
contextweaver decides what to surface this turn and how to compact
oversized tool outputs (the context firewall).

## Decision matrix

| Backend | Best for | Status | Install |
|---|---|---|---|
| **Mem0** | Passive memory extraction from conversations, multi-tenant deployments | Available | `pip install 'contextweaver[mem0]'` |
| **Zep / Graphiti** | Temporal knowledge graphs, time-aware fact retrieval | Planned (issue [#195](https://github.com/dgenio/contextweaver/issues/195)) | `pip install 'contextweaver[zep]'` |
| **LangMem** | LangGraph-native episodic / semantic / procedural memory split | Planned (issue [#195](https://github.com/dgenio/contextweaver/issues/195)) | `pip install 'contextweaver[langmem]'` |

All three implement the **same** existing protocols
(`EpisodicStore` / `FactStore` from `contextweaver.store.protocols`)
without widening them, so the wiring is identical across backends
once their respective adapters land.

## Boundary diagram

```text
┌──────────────────────────────────────────────────────────────┐
│ Your agent runtime (any framework)                            │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ contextweaver — policy layer                           │  │
│  │                                                        │  │
│  │   ContextManager.build()                               │  │
│  │     ├─ EpisodicStore.search(query)  ─┐                 │  │
│  │     └─ FactStore.get_by_key(key)    ─┤                 │  │
│  │                                       │                │  │
│  └───────────────────────────────────────┼────────────────┘  │
│                                          ▼                   │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ External memory service (Mem0 / Zep / LangMem)         │  │
│  │  • Long-lived store across sessions                    │  │
│  │  • Vector / graph recall                               │  │
│  │  • Memory extraction / consolidation                   │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

contextweaver never reaches outward — the adapter wraps the **client
object you already hold** (a configured `mem0.Memory`, etc.) and routes
protocol calls through it.

## Mem0

`contextweaver.extras.memory.mem0` ships two classes:

- `Mem0EpisodicStore` — `EpisodicStore` Protocol
- `Mem0FactStore` — `FactStore` Protocol

Both wrap a `mem0.Memory` instance scoped by a stable `user_id`. Writes
go through `Memory.add(... infer=False)` so mem0 does **not** run an
LLM extraction pass — the raw `Episode.summary` / `Fact.value` is stored
as-supplied. Every record is stamped with a contextweaver metadata
key (`cw_episode_id` / `cw_fact_id`) so `get` / `delete` can resolve
the canonical ID back to mem0's UUID.

### Minimal wiring

```python
from mem0 import Memory

from contextweaver.extras.memory.mem0 import Mem0EpisodicStore, Mem0FactStore
from contextweaver.context.manager import ContextManager
from contextweaver.store import StoreBundle
from contextweaver.store.episodic import Episode
from contextweaver.store.facts import Fact

# 1. Configure mem0 — bring your own LLM / vector store / reranker.
memory = Memory()

# 2. Adapt mem0 onto contextweaver's protocols.  user_id is the mem0
#    session id used for scope partitioning; use a stable value per
#    agent / per tenant.
episodic = Mem0EpisodicStore(memory, user_id="agent:support-bot")
facts = Mem0FactStore(memory, user_id="agent:support-bot")

# 3. Plug into the StoreBundle the ContextManager uses.
bundle = StoreBundle(episodic_store=episodic, fact_store=facts)
ctx_mgr = ContextManager(stores=bundle)

# 4. From here on, use the standard contextweaver API.
episodic.add(Episode("ep-1", "User asked about refund policy for SKU-42"))
facts.put(Fact("f-tier", key="user.tier", value="enterprise"))
```

### Search semantics

`Mem0EpisodicStore.search` delegates to `Memory.search` and inherits
mem0's vector + reranker stack — this is the main reason to choose
mem0 over the bundled `InMemoryEpisodicStore`. The configured
`user_id` scope is applied at search time so two adapter instances
constructed with different `user_id`s don't see each other's records.

### Mem0FactStore design notes

mem0 has no first-class concept of a `key` separate from the content
itself, so `Mem0FactStore.get_by_key` and `list_keys` reconstruct the
answer client-side by scanning `Memory.get_all`. When the configured
`user_id` scope exceeds `scan_limit` (default `1000`), these methods
raise `NotImplementedError` rather than silently truncating. Narrow
scope by partitioning across multiple `user_id`s or pick a dedicated
`FactStore` backend (in-memory or SQLite).

### Out-of-scope (current cycle)

`Mem0EpisodicStore.search` does **not** currently expose mem0's
threshold / rerank parameters — both will surface once the protocol
gains a `search_options` parameter (tracked separately; this PR
intentionally does not widen the Protocol).

## Zep / Graphiti

Same protocol shape as Mem0; deferred to a follow-up cycle. See issue
[#195](https://github.com/dgenio/contextweaver/issues/195) for the
acceptance criteria. The adapter will live at
`contextweaver.extras.memory.zep` once it lands.

When implemented, the wiring becomes:

```python
# Planned — module not shipped yet.
# from contextweaver.extras.memory.zep import ZepEpisodicStore, ZepFactStore
```

## LangMem

Same shape; deferred. Will live at
`contextweaver.extras.memory.langmem` once landed. Issue
[#195](https://github.com/dgenio/contextweaver/issues/195) covers the
acceptance criteria.

## See also

- [`tests/test_extras_memory_mem0.py`](https://github.com/dgenio/contextweaver/blob/main/tests/test_extras_memory_mem0.py) —
  Reference for the wire shape and the per-method semantics.
- [`docs/integration_otel.md`](integration_otel.md) — Same
  `extras/` pattern, different responsibility (observability vs.
  memory).
- [How contextweaver Fits](interop.md) — Where the policy layer sits
  relative to runtimes and persistence layers.

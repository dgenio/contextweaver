# Comparison & Memory Systems

This page explains how contextweaver's in-session memory works, what it
does not do out of the box, and how to plug in a third-party backend.

---

## Memory systems

### What contextweaver does in-session

contextweaver provides two in-session memory primitives:

- **`FactStore`** — stores key-value facts (e.g. `user_timezone=UTC`)
  injected into every prompt. Written via `add_fact_sync`.
- **`EpisodicStore`** — stores short summaries of past interactions,
  keyed by episode ID, injected into the prompt header.
  Written via `add_episode_sync`.
- **`EventLog`** — append-only history of every `ContextItem` in the
  current session.

Both stores are capped in the prompt to prevent memory from crowding out
the current conversation. See [Concepts → Episodic Memory & Facts](concepts.md#episodic-memory--facts)
for field-level details.

### What contextweaver does NOT do

contextweaver does **not** provide cross-session persistence out of the
box. When the process restarts, in-memory `FactStore` and `EpisodicStore`
contents are lost unless you wire a persistent backend yourself.

### Decision matrix

| Use case | Recommended backend |
|---|---|
| Simple key-value facts, single process | Built-in `FactStore` (in-memory) |
| Short-term episode summaries, single process | Built-in `EpisodicStore` (in-memory) |
| Cross-session memory, user-scoped recall | [Mem0](https://mem0.ai) |
| Long-term episodic + semantic search | [Zep](https://getzep.com) |
| LangChain-native memory with summarisation | [LangMem](https://github.com/langchain-ai/langmem) |
| Full control, lightweight persistence | Custom SQLite store via the plug-in protocol below |

### Plug-in shape

You can replace either built-in store by implementing the protocol and
passing it at construction time:

```python
from contextweaver.store import EpisodicStore

class MyZepStore(EpisodicStore):
    def add_episode_sync(self, episode_id: str, summary: str) -> None:
        zep_client.add(episode_id, summary)

    def get_episodes_sync(self, episode_id: str) -> list[str]:
        return zep_client.get(episode_id)

weaver = ContextWeaver(episodic_store=MyZepStore())
```

First-class adapters for Mem0, Zep, and LangMem are tracked in
[#195 — External memory backend interop](https://github.com/dgenio/contextweaver/issues/195).

---

### See also

- [Concepts → Episodic Memory & Facts](concepts.md#episodic-memory--facts)
- [FAQ](faq.md)
- [#195 — External memory backend interop (Mem0 / Zep / LangMem)](https://github.com/dgenio/contextweaver/issues/195)
# Catalog showcase — reference architecture

> The smallest end-to-end demonstration of why contextweaver exists. A
> **65-tool** catalog is narrowed to a **5-card shortlist** for one user
> request, only the selected tool's schema is hydrated, and a large tool
> result is firewalled to a compact summary — so the prompt stays bounded
> no matter how big the catalog or the result.

## Run it

```bash
python examples/architectures/catalog_showcase/main.py
```

(Or `make architectures` / `make example`.)

A captured run of the script lives in [`OUTPUT.md`](OUTPUT.md).

## What this is (and isn't)

This is the **first architecture to read**. It is deliberately linear —
one request, four steps — so a new adopter sees the core value before
learning every concept. The other architectures (code-review bot, Slack
ops bot, voice agent) layer multi-turn state and richer transcripts on
top of the same primitives.

It is **deterministic and offline**: the bulk of the catalog is generated
from a fixed seed (`generate_sample_catalog(n=62, seed=7)`), the three
schema-rich "hero" tools are defined inline in [`main.py`](main.py), the
tool result is canned, and no language model or network call happens.

## The four steps

1. **Route → shortlist.** `Router.route(request)` narrows 65 tools to a
   5-card shortlist. The model only ever sees compact `ChoiceCard`s —
   never the full catalog and never any argument schema.
2. **Expand only the selected tool.** After the shortlist is chosen,
   `Catalog.hydrate(chosen)` resolves the full `args_schema` for the one
   selected tool (653 chars). The other 64 tools cost zero schema bytes.
3. **Firewall a large result.** A ~3 KB product-search payload is ingested
   via `ingest_tool_result_sync(...)`; the firewall replaces it with a
   ~500-char summary (an 84% prompt-side reduction) and keeps the raw
   bytes addressable in the artifact store.
4. **Final answer pack.** `build_sync(phase=Phase.answer, ...)` assembles
   the budget-aware prompt; `BuildStats` shows exactly what was kept.

## What's load-bearing

| contextweaver feature | Used | What it does here |
|---|---|---|
| `Router.route(query)` | ✅ | Narrows 65 tools → top-5 shortlist (`top_k=5`) |
| `ChoiceCard` rendering | ✅ | The model sees 5 compact cards with **no** argument schemas |
| `Catalog.hydrate(tool_id)` | ✅ | Lazily resolves the full schema for the **one** selected tool |
| **Context firewall** | ✅✅ | Compacts the ~3 KB search payload to a ~500-char summary before it touches the prompt |
| Artifact store | ✅ | Raw bytes stay addressable for drilldown; only the summary lands in the prompt |
| `ContextBudget` | ✅ | `ContextBudget(route=1500, call=2500, interpret=2500, answer=3000)` keeps every phase bounded |

## What's intentionally not here

- **Real tool execution.** The product-search result is canned to keep the
  example deterministic. A real deployment would call a backend or an MCP
  server and pipe the response through the same firewall.
- **Multi-turn state.** This is a single request. See the
  [code-review bot](../code_review_bot/README.md) and
  [Slack ops bot](../slack_ops_bot/README.md) for persistent facts and
  multi-step transcripts.

## Read next

- The [60-second killer demo](../../../docs/quickstart.md) (`contextweaver
  demo --scenario killer`) makes the same point as a before/after token
  comparison.
- The [cookbook](../../../docs/cookbook.md) covers the individual
  primitives — routing, firewall, drilldown — used here.
- [`docs/architectures/catalog_showcase.md`](../../../docs/architectures/catalog_showcase.md)
  is the public-docs version of this README.

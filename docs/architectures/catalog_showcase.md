# Catalog showcase

> The smallest end-to-end demonstration of why contextweaver exists. A
> **65-tool** catalog is narrowed to a **5-card shortlist** for one user
> request, only the selected tool's schema is hydrated, and a large tool
> result is firewalled — so the prompt stays bounded no matter how big the
> catalog or the result. **Start here** before the other architectures.

## TL;DR

| What | Where |
|---|---|
| The script | [`examples/architectures/catalog_showcase/main.py`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/catalog_showcase/main.py) |
| Captured output | [`examples/architectures/catalog_showcase/OUTPUT.md`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/catalog_showcase/OUTPUT.md) |
| Local README | [`examples/architectures/catalog_showcase/README.md`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/catalog_showcase/README.md) |

Run it:

```bash
python examples/architectures/catalog_showcase/main.py
```

(Or `make architectures` / `make example`.)

## The four steps

1. **Route → shortlist.** `Router.route(request)` narrows 65 tools to a
   5-card shortlist. The model only ever sees compact
   [`ChoiceCard`](../tool_router.md)s — never the full catalog, never any
   argument schema.
2. **Expand only the selected tool.** `Catalog.hydrate(chosen)` resolves the
   full `args_schema` for the one selected tool (653 chars). The other 64
   tools cost zero schema bytes.
3. **Firewall a large result.** A ~3 KB product-search payload is ingested;
   the [context firewall](../context_firewall.md) replaces it with a
   ~500-char summary (an 84% prompt-side reduction) and keeps the raw bytes
   addressable in the artifact store.
4. **Final answer pack.** `build_sync(phase=Phase.answer, ...)` assembles the
   budget-aware prompt; `BuildStats` shows exactly what was kept.

## What's load-bearing

| Feature | What it does here |
|---|---|
| `Router.route(query)` | Narrows 65 tools → top-5 shortlist (`top_k=5`) |
| `ChoiceCard` rendering | The model sees 5 compact cards with **no** schemas |
| `Catalog.hydrate(tool_id)` | Lazily resolves the full schema for the **one** selected tool |
| Context firewall | Compacts the ~3 KB result to a ~500-char summary |
| Artifact store | Raw bytes stay addressable for drilldown |

## Determinism

The bulk of the catalog is generated from a fixed seed
(`generate_sample_catalog(n=62, seed=7)`), the three schema-rich hero tools
are defined inline, the tool result is canned, and no model or network call
happens. Per-section token counts depend on the active tokeniser; every
other number is character- or count-based and stable across environments.

## Read next

- The [60-second killer demo](../killer_demo.md) makes the same point as a
  before/after token comparison.
- The [code-review bot](code_review_bot.md) and [Slack ops bot](slack_ops_bot.md)
  layer multi-turn state on top of these same primitives.

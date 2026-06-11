# Tool Router

The Tool Router is contextweaver's bounded-choice navigation engine for
large tool catalogs. It turns a flat catalog of N tools (commonly
50–500+) into a deterministic, beam-searched shortlist of K candidate
`ChoiceCard`s — small, LLM-friendly cards that never carry full tool
schemas.

```
                ┌───────────────────────────────────────┐
   Catalog ────>│  Catalog → TreeBuilder → ChoiceGraph  │──> ChoiceCards (k=5)
   (100 tools)  │      → Router (beam search)           │    ~500 prompt tokens
                └───────────────────────────────────────┘
```

## Why bounded-choice routing

Putting all tool schemas into every prompt is the obvious approach and
the wrong one once a catalog passes ~20 tools:

1. **Cost.** 100 tool schemas (≈50 K tokens) at GPT-4o rates is roughly
   $0.48 per request — before any user text.
2. **Latency.** Time-to-first-token grows linearly with prompt size;
   3–5 s TTFT on 80 K-token prompts is typical.
3. **Quality.** Selection accuracy degrades as the catalog grows; the
   LLM hallucinates tool names and confuses similar tools.

The router scopes the choice set deterministically: a beam search over a
bounded `ChoiceGraph` produces a top-K shortlist plus a confidence gap
between rank-1 and rank-2. The LLM picks from K cards, not from N
schemas.

## Pipeline

The router runs a four-stage pipeline (introduced in v0.7, #56) — each
stage is swappable via the `RoutingPipeline` composer:

1. **Retrieve.** TF-IDF / BM25 / fuzzy / embedding scoring against the
   active catalog. Default zero-dependency path is TF-IDF + tag /
   namespace lexical floor.
2. **Rerank.** Optional history-aware rerank that deprioritises
   already-called tools, boosts candidates resembling the most recent
   tool-result summary, and applies `depends_on` / `provides` /
   `requires` adjustments.
3. **Navigate.** Beam search over the bounded `ChoiceGraph` DAG with
   deterministic tie-breaking by `id`.
4. **Pack.** Render K `ChoiceCard`s, token-native against `cl100k_base`
   per `gateway_spec.md` §2.3 (target ≤ 60 tokens, hard cap ≤ 80 tokens
   per card).

`ChoiceCards` never include full input schemas. When the LLM commits to
a tool the runtime hydrates the schema on demand via
`Catalog.hydrate(tool_id)`.

## Worked examples

- [`examples/routing_demo.py`](https://github.com/dgenio/contextweaver/blob/main/examples/routing_demo.py)
  — 40-line minimal routing call against a 40-tool catalog.
- [`examples/fastmcp_discovery_demo.py`](https://github.com/dgenio/contextweaver/blob/main/examples/fastmcp_discovery_demo.py)
  — 22 tools shrinking to a 3-tool shortlist (86 % token reduction)
  via the FastMCP CodeMode discovery hook.
- [`examples/mcp_gateway_demo.py`](https://github.com/dgenio/contextweaver/blob/main/examples/mcp_gateway_demo.py)
  — MCP-wire `tool_browse` / `tool_execute` / `tool_view` meta-tools
  produced by the router.
- [`examples/architectures/slack_ops_bot/main.py`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/slack_ops_bot/main.py)
  — 48-tool catalog narrowed to 3 cards per turn across a six-turn
  scripted Slack incident-response transcript.

## Quick wire-up

```python
from contextweaver.routing.catalog import load_catalog_yaml
from contextweaver.routing.router import Router

catalog = load_catalog_yaml("examples/sample_catalog.yaml")
router = Router(catalog=catalog)
result = router.route(query="send a reminder email", top_k=5)

for card in result.choice_cards:
    print(card.id, card.name, card.tags, f"score={card.score:.2f}")
```

The router is deterministic by default — same catalog + query → byte-identical
`ChoiceCard` JSON. This is intentional and locked by
`tests/test_cards.py::test_make_choice_cards_byte_identical_stable_order`
so the cards can be reused as a stable `cache_control` prefix in
Anthropic / OpenAI / Google prompt-caching deployments.

## Lint before you deploy

Catalog metadata quality drives routing quality: items with empty descriptions,
duplicate names, or dangling `depends_on` / `requires` references route badly.
Run the linter as an authoring-time / CI gate (issue #538):

```bash
contextweaver catalog lint examples/sample_catalog.json        # human-readable
contextweaver catalog lint catalog.yaml --json                 # machine-readable
```

It accepts the native JSON/YAML catalog, a raw MCP `tools/list` array, and the
`{"tools": [...]}` snapshot shape, runs the `CatalogNormalizer` plus cross-item
reference validation, and exits `0` (clean) / `1` (findings) / `3` (load error)
— so it slots straight into a pre-commit hook or CI step. Input files are never
modified.

The same referential check runs on load (issue #519). The `load_catalog*`
loaders take an `on_invalid` policy:

```python
from contextweaver.routing.catalog import load_catalog

# "warn" (default): log each dangling reference, return items anyway.
items = load_catalog("catalog.yaml")

# "raise": fail loudly — the CatalogValidationError carries the full report.
items = load_catalog("catalog.yaml", on_invalid="raise")
```

## Reference

- [`Concepts`](concepts.md) — `SelectableItem`, `ChoiceCard`, `ChoiceGraph`.
- [`Architecture`](architecture.md) — Routing Engine internals.
- [`Gateway Spec`](gateway_spec.md) — `tool_id` grammar, ChoiceCard size
  bounds, `tool_browse` path grammar.
- API: `contextweaver.routing.router.Router`, `contextweaver.routing.pipeline`.

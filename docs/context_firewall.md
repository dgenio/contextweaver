# Context Firewall

The Context Firewall is contextweaver's load-bearing pattern for keeping
large or sensitive tool outputs out of the prompt while keeping them
addressable from later turns. Raw bytes go to the artifact store; the
LLM sees a compact summary, a typed handle, and any extracted structured
fields.

The firewall reduces prompt exposure; it does not erase or authorize access
to the stored bytes. Read the
[MCP Gateway Security Model](security_model.md) before deploying it with
sensitive upstreams.

```
                        ┌─────────────────────────────┐
   Raw tool output ────>│       Context Firewall      │──> Summary + handle to LLM
   (e.g. 28 KB log)     │  (length-gated, deterministic) │    (e.g. ~500 chars)
                        └──────────────┬──────────────┘
                                       │
                                       v
                              ┌────────────────────┐
                              │   Artifact Store   │
                              │  Raw bytes by ref  │
                              └────────────────────┘
```

## Why a firewall

A naive agent loop that concatenates raw tool outputs into the next
prompt has three failure modes:

1. **Token bloat.** One 30 KB log dump consumes most of a 32 K context
   window.
2. **Quality drop.** Needle-in-haystack accuracy degrades with prompt
   size; the LLM loses focus on the actual question.
3. **Sensitivity leakage.** Raw outputs may contain credentials, PII,
   or internal URLs that the LLM should not see verbatim.

The firewall addresses all three: every tool result above a configurable
size threshold is intercepted, summarized, and stored out-of-band. The
LLM sees only the summary plus a typed `ArtifactRef` it can pass back if
it needs the full bytes later.

## How to enable it

The firewall is on by default for any tool result ingested via
`ContextManager.ingest_tool_result_sync()` or the firewall hook in
provider adapters. Configuration lives on `FirewallConfig`:

```python
from contextweaver.config import ContextPolicy, FirewallConfig

policy = ContextPolicy(
    firewall=FirewallConfig(
        size_threshold_bytes=2048,   # outputs above this get firewalled
        summary_max_chars=500,       # cap on the rendered summary
    )
)
```

See [`docs/architecture.md`](architecture.md) §"Context Engine pipeline"
for the eight-stage build sequence the firewall sits inside, and
[`docs/cookbook.md`](cookbook.md) §4 "Firewall + drilldown" for a
runnable recipe.

> **Two firewalls?** In a full Weaver Stack, agent-kernel firewalls at the
> *execution* boundary and hands contextweaver a `Frame`; contextweaver then
> firewalls the *context budget*. See
> [Firewall Boundary (Frame seam)](context_firewall_boundary.md) for who
> firewalls what and the canonical `ingest_envelope()` path.

## Single-call firewall (`compact_tool_result`)

When you just have **one** large tool result and want to shrink it before it
enters the prompt — without standing up a `ContextManager` or a synthetic
turn — use the single-call facade (issue #399):

```python
from contextweaver import compact_tool_result

out = compact_tool_result(
    {"invoices": [...]},
    threshold_chars=2000,
    keep=["invoices[].invoiceNumber", "invoices[].amount", "invoices[].status"],
)
out.firewalled          # True
out.payload             # projected subset + {"_cw": {...}} sidecar
out.stats.tokens_saved  # how much stayed out of the prompt
```

It composes the firewall primitives:

- **Schema-preserving pass-through** (issue #403). When the payload is at or
  below `threshold_chars`, the caller's shape is returned **unchanged** —
  same keys, same nesting — with firewall metadata attached only on a
  reserved, namespaced `_cw` sidecar key (dicts) and never an in-place rewrite.
  Lists and strings are returned byte-identical. Downstream code that reads
  `result.response.x` keeps working whether or not the firewall fired.
- **Structured (lossless) mode** (issue #406). Pass a `keep` JSON-path
  allow-list (or `strategy="structured"`) and the firewall keeps only the
  allow-listed paths inline, offloads the full payload to the artifact store,
  and leaves the dropped fields retrievable via `drilldown`. This is
  deterministic and performs **no LLM call** — the right primitive for
  structured line-of-business data (billing, CRM, catalog lookups).
- **Determinism guarantee** (issue #404). `deterministic=True` (the default
  for this facade) *fails closed*: if the chosen path would invoke an
  LLM-backed summariser it raises `DeterminismError` instead of silently
  passing data through a model.
  `FirewallStats.summarized_by_llm` / `strategy` record exactly what happened,
  so the guarantee is observable and citable in a compliance review.
- **Built-in token counter** (issue #405). Savings are measured with
  `contextweaver.tokens.count` — the same counter the firewall uses
  internally — so reported numbers match what callers measure. `tiktoken` is
  a core dependency and degrades to a character heuristic offline.

### Firewall diagnostics (`FirewallStats`)

Every firewall decision now records a `FirewallStats` (issue #402) answering
the two questions an integrator cares about — *was the firewall triggered?*
and *how much was saved?*:

```python
mgr = ContextManager()
mgr.ingest_sync(ContextItem(id="result:tc1", kind=ItemKind.tool_result, text=big))
pack = mgr.build_sync(phase=Phase.interpret, query="...")

fs = pack.stats.firewall_summary()   # roll-up across the build
fs.triggered, fs.strategy            # True, "summary"
fs.chars_saved, fs.tokens_saved      # how much stayed out of the prompt
pack.stats.firewall_events           # per-item FirewallStats
```

`ResultEnvelope.firewall_stats` carries the same per-result diagnostics on the
ingest path. Pass `ContextManager(deterministic=True)` to extend the
fail-closed guarantee to the whole build pipeline, and
`ingest_tool_result(..., firewall=StructuredFirewall(keep=[...]))` to select
structured projection at ingest time.

## Drilling down to raw bytes

`ArtifactRef` supports four built-in drilldown selectors so the LLM can
request a specific slice without rehydrating the entire artifact:

| Selector | What it returns |
|---|---|
| `head` | First N bytes / characters |
| `lines` | A line range (`start..end`) |
| `json_keys` | One or more top-level keys from a JSON document |
| `rows` | A row range from a CSV / JSONL document |

The drilldown selectors are byte-identical across `InMemoryArtifactStore`
and `JsonFileArtifactStore` (enforced by a shared
`src/contextweaver/store/artifacts.py::_apply_selector` helper).

## Worked examples

- [`examples/cookbook/firewall_drilldown_recipe.py`](https://github.com/dgenio/contextweaver/blob/main/examples/cookbook/firewall_drilldown_recipe.py)
  — 80-line cookbook recipe that builds a synthetic 30 KB log, fires
  the firewall, and then performs three drilldowns from a follow-up
  turn.
- [`examples/architectures/slack_ops_bot/main.py`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/slack_ops_bot/main.py)
  — production-shape architecture where the firewall is the
  load-bearing primitive for a 34 KB log dump in turn 2.
- [`examples/architectures/code_review_bot/main.py`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/code_review_bot/main.py)
  — pull-request review where the firewall handles a ~28 KB diff dump
  and a ~2.5 KB grep result, both compacting to ~500-char summaries.

## Reference

- [`Concepts`](concepts.md) — `ArtifactRef`, `ContextItem`, sensitivity
  levels.
- [MCP Gateway Security Model](security_model.md) — storage, view, egress,
  and authorization boundaries.
- [`Architecture`](architecture.md) — full pipeline placement.
- [`Cookbook`](cookbook.md) §4 — drilldown patterns.
- API: `contextweaver.context.firewall`, `contextweaver.config.FirewallConfig`.

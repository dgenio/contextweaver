# Context Firewall

The Context Firewall is contextweaver's load-bearing pattern for keeping
large or sensitive tool outputs out of the prompt while keeping them
addressable from later turns. Raw bytes go to the artifact store; the
LLM sees a compact summary, a typed handle, and any extracted structured
fields.

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
- [`Architecture`](architecture.md) — full pipeline placement.
- [`Cookbook`](cookbook.md) §4 — drilldown patterns.
- API: `contextweaver.context.firewall`, `contextweaver.config.FirewallConfig`.

# Voice agent — reference architecture

> A real-time customer-service voice bot fronting ~18 tools.
> Demonstrates the **`asyncio.to_thread(mgr.build_sync, …)`** pattern
> documented in [`docs/integration_pipecat.md`](../../../docs/integration_pipecat.md),
> plus tight per-phase budgets that keep TTS under a 300 ms response
> bound.

## Run it

```bash
python examples/architectures/voice_agent/main.py
```

(Or `make architectures` / `make example`.)

A captured run of the script lives in [`OUTPUT.md`](OUTPUT.md).

## What this is (and isn't)

This is a **reference architecture**, not a tutorial recipe. The cookbook
gives you copy-paste snippets for individual primitives; the
architecture wires them together around a realistic problem shape.

It is **mocked**: tool implementations return canned strings, no real
audio pipeline or backend systems are touched. The point is to
demonstrate the contextweaver / Pipecat glue, not to integrate with a
specific transport.

Pipecat is **optional**: the script runs end-to-end without it. When
`pipecat-ai` is installed (via `pip install 'contextweaver[voice]'`)
the script reports the install and you can wire the same context
manager into a real Pipecat `FrameProcessor` — see
[`docs/integration_pipecat.md`](../../../docs/integration_pipecat.md)
for the worked frame-processor code.

## Setup

The 18-tool catalog lives in [`catalog.yaml`](catalog.yaml). Loading it:

```python
from contextweaver.routing.catalog import Catalog, load_catalog_yaml

catalog = Catalog()
for item in load_catalog_yaml("catalog.yaml"):
    catalog.register(item)
```

Namespaces: `support`, `orders`, `shipping`, `account`, `callback`.
Several tools have side effects (`orders.modify`,
`shipping.update_address`, `callback.schedule`, …); the catalog records
this on each `SelectableItem.side_effects` so a real deployment could
refuse to call them automatically
(`Router.route(..., exclude_tags=...)`).

## The call

The bot walks a five-turn customer-service call:

1. *"hi, can you look up order number A-481 for me"* — `orders.lookup`
2. *"what is the shipping tracking status for that order"* — `shipping.tracking`
3. *"can you change the delivery address to my new home"* — `shipping.update_address`
4. *"when is the next available delivery slot"* — `shipping.delivery_slot`
5. *"schedule a callback for me at 2pm tomorrow"* — `callback.schedule`

See `TRANSCRIPT` in [`main.py`](main.py) for the exact text.

## What's load-bearing

| contextweaver feature | Used | What it does here |
|---|---|---|
| `Router.route(query)` | ✅ | Narrows 18 tools → top-3 shortlist (`top_k=3`) |
| Bounded choice pattern | ✅ | Bot picks from the shortlist, not from the whole catalog |
| **`asyncio.to_thread(mgr.build_sync, …)`** | ✅ | All context builds run on a worker thread so the audio pipeline event loop stays free |
| **Tight per-phase budgets** | ✅ | `ContextBudget(route=200, call=500, interpret=400, answer=1000)` keeps every prompt small enough for sub-300 ms TTS |
| Persistent facts | ✅ | `customer.order_id`, `customer.shipping_address`, `customer.callback` survive across all five turns |
| Artifact store | ✅ | Tool results are addressable for drilldown even though they're small |

## The async pattern

contextweaver's context pipeline is sync (deterministic, no IO). For
real-time pipelines you want it off the audio event loop. The canonical
pattern, used in this example and documented in the Pipecat guide:

```python
async def _async_build(mgr, *, phase, query):
    return await asyncio.to_thread(mgr.build_sync, phase=phase, query=query)
```

The routing call (`router.route(query)`) is fast enough — sub-millisecond
for catalogs in the 10–100 range — that you can keep it on the audio
thread.

## What's intentionally not here

- **A live audio pipeline.** The script is text-only; the per-turn
  output simulates STT (text frames). For a worked Pipecat
  `FrameProcessor`, see
  [`docs/integration_pipecat.md`](../../../docs/integration_pipecat.md).
- **TTS latency measurement.** The script prints "off-thread" timings
  for the context build, which is the part contextweaver controls.
  TTS / network IO is the model's / transport's responsibility.
- **Across-call session state.** The fact store survives the in-process
  call, but to persist across calls you would swap the default
  `InMemoryFactStore` for a `SqliteFactStore`
  ([issue #174](https://github.com/dgenio/contextweaver/issues/174)) or
  a Mem0 / Zep adapter
  ([issue #195](https://github.com/dgenio/contextweaver/issues/195)).

## Read next

- The [Pipecat integration guide](../../../docs/integration_pipecat.md) —
  worked `FrameProcessor` against the same patterns this architecture
  uses.
- The [cookbook](../../../docs/cookbook.md) covers the individual
  primitives — routing, firewall, drilldown — used here.

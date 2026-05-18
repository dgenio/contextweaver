# Voice agent (Pipecat)

> Production reference architecture for a real-time customer-service
> voice bot. Demonstrates the
> **`asyncio.to_thread(mgr.build_sync, …)`** pattern documented in
> [the Pipecat integration guide](../integration_pipecat.md), plus tight
> per-phase budgets that keep TTS responsive under a 300 ms latency
> bound.

## TL;DR

| What | Where |
|---|---|
| The script | [`examples/architectures/voice_agent/main.py`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/voice_agent/main.py) |
| The catalog | [`examples/architectures/voice_agent/catalog.yaml`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/voice_agent/catalog.yaml) |
| Captured output | [`examples/architectures/voice_agent/OUTPUT.md`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/voice_agent/OUTPUT.md) |
| Local README | [`examples/architectures/voice_agent/README.md`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/voice_agent/README.md) |
| Companion guide | [`docs/integration_pipecat.md`](../integration_pipecat.md) |

Run it:

```bash
python examples/architectures/voice_agent/main.py
```

(Or `make architectures` / `make example`.)

For the optional Pipecat hook:

```bash
pip install 'contextweaver[voice]'
```

## The shape

The bot walks a five-turn customer-service call (order chase, address
update, callback scheduling):

1. *"hi, can you look up order number A-481 for me"* — `orders.lookup`
2. *"what is the shipping tracking status for that order"* — `shipping.tracking`
3. *"can you change the delivery address to my new home"* — `shipping.update_address`
4. *"when is the next available delivery slot"* — `shipping.delivery_slot`
5. *"schedule a callback for me at 2pm tomorrow"* — `callback.schedule`

For each turn:

- The [`Router`](../architecture.md#routing-engine) narrows 18 tools to
  a top-3 shortlist (`top_k=3`). Routing is sub-millisecond and runs
  on the audio thread.
- The bot picks one tool *from the shortlist* using an explicit intent
  map. That separation is the **load-bearing pattern**: contextweaver
  bounds the choice, the bot (or, in production, an LLM) makes the
  final selection.
- **Every context build runs via `asyncio.to_thread(mgr.build_sync,
  …)`** — keeping the audio event loop free while the prompt is
  assembled on a worker thread.
- Persistent [facts](../concepts.md) (`customer.order_id`,
  `customer.shipping_address`, `customer.callback`) survive across all
  five turns of the call.

## What's load-bearing

| contextweaver feature | Used | What it does here |
|---|---|---|
| `Router.route(query)` | ✅ | Narrows 18 tools → top-3 shortlist (`top_k=3`) |
| Bounded choice pattern | ✅ | Bot picks from the shortlist, not from the whole catalog |
| **`asyncio.to_thread(mgr.build_sync, …)`** | ✅ | Every context build runs on a worker thread so the audio pipeline event loop stays free |
| **Tight per-phase budgets** | ✅ | `ContextBudget(route=200, call=500, interpret=400, answer=1000)` keeps every prompt small enough for sub-300 ms TTS |
| Persistent facts | ✅ | Three fact keys survive across all five turns of the call |

## The async pattern in detail

contextweaver's context pipeline is sync (deterministic, no IO). For
real-time pipelines you want it off the audio event loop. The canonical
pattern, used in this example and documented in the
[Pipecat integration guide](../integration_pipecat.md):

```python
async def _async_build(mgr, *, phase, query):
    return await asyncio.to_thread(mgr.build_sync, phase=phase, query=query)
```

Why this works:

- The `_build` pipeline (eight sync stages — see the
  [architecture overview](../architecture.md#context-engine)) is pure
  Python computation; no awaits, no IO.
- Wrapping it in `asyncio.to_thread` hands it to the default executor
  so the event loop can continue draining audio frames.
- The routing call (`router.route(query)`) is fast enough — typically
  sub-millisecond for catalogs in the 10–100 range — that you can keep
  it on the audio thread without measurable jitter.

## What's intentionally not here

- **A live audio pipeline.** The script is text-only; the per-turn
  output simulates STT. For a worked Pipecat `FrameProcessor`, see the
  [Pipecat integration guide](../integration_pipecat.md).
- **TTS latency measurement.** The script prints "off-thread" timings
  for the context build, which is the part contextweaver controls.
  TTS / network IO is the model's / transport's responsibility.
- **Across-call session state.** The fact store survives the in-process
  call. To persist across calls, swap the default `InMemoryFactStore`
  for a `SqliteFactStore`
  ([issue #174](https://github.com/dgenio/contextweaver/issues/174)) or
  a Mem0 / Zep adapter
  ([issue #195](https://github.com/dgenio/contextweaver/issues/195)).

## Read next

- The [Pipecat integration guide](../integration_pipecat.md) — worked
  `FrameProcessor` against the same patterns this architecture uses.
- [Slack ops bot](slack_ops_bot.md) and
  [code-review bot](code_review_bot.md) — the other two reference
  architectures in the series.
- The [cookbook](../cookbook.md) covers the individual primitives —
  routing, firewall, drilldown — used here.

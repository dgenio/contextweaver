# Pipecat Integration

> Pair contextweaver's async context compilation and context firewall
> with [Pipecat](https://docs.pipecat.ai/) so real-time voice and video
> agents stay budget-aware and don't stall the pipeline on a single
> multi-KB function result.

## Why

Pipecat's pipeline architecture is designed for sub-100 ms turn-taking.
Three things break that latency budget on long calls:

- **Unbounded conversation history.** Every turn is appended verbatim;
  by minute 10 the prompt is enormous.
- **Function-result stalls.** A single large response (50 orders, an
  embedding result, a database dump) blocks the LLM frame until it
  fits the model's input.
- **Loading every function into the prompt.** Every available function
  is in the system instructions on every turn.

contextweaver fixes all three asynchronously: `ContextManager.build()`
is awaitable so it slots into a Pipecat `FrameProcessor` without
serialising the pipeline.

## Prerequisites

```bash
pip install contextweaver pipecat-ai openai
export OPENAI_API_KEY=sk-...
# Optional: Daily.co transport
export DAILY_API_KEY=...
```

## Architecture

```text
Audio Input
   │
   ▼
[VAD]                                  ← voice activity detection
   │
   ▼
[STT]                                  ← speech-to-text (TextFrame)
   │
   ▼
[contextweaver FrameProcessor]
   │   ─ ctx_mgr.ingest (user turn)
   │   ─ router.route → shortlist
   │   ─ await ctx_mgr.build(phase=Phase.call)
   ▼
[LLM]                                  ← receives pack.prompt + shortlist
   │
   ▼ (function call)
[Function execution]
   │   ─ ctx_mgr.ingest_tool_result (firewall)
   │   ─ await ctx_mgr.build(phase=Phase.answer)
   ▼
[TTS]                                  ← text-to-speech
   │
   ▼
Audio Output
```

You hook contextweaver in via a custom `FrameProcessor` that sits
between STT and the LLM. The async `build()` runs concurrently with
TTS / network IO so the pipeline doesn't block.

## Async-aware FrameProcessor

```python
from __future__ import annotations

from pipecat.frames.frames import (
    FunctionCallResultFrame,
    LLMMessagesFrame,
    TextFrame,
)
from pipecat.processors.frame_processor import FrameProcessor

from contextweaver.context.manager import ContextManager
from contextweaver.routing.catalog import Catalog
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import ContextItem, ItemKind, Phase, SelectableItem


class ContextWeaverProcessor(FrameProcessor):
    """Pipecat frame processor that drives the contextweaver pipeline."""

    def __init__(self, catalog: Catalog, ctx_mgr: ContextManager) -> None:
        super().__init__()
        self._catalog = catalog
        self._ctx_mgr = ctx_mgr
        graph = TreeBuilder(max_children=8).build(catalog.all())
        self._router = Router(graph, items=catalog.all(), top_k=3)
        self._turn = 0

    async def process_frame(self, frame, direction):
        if isinstance(frame, TextFrame):
            # 1. User said something (or your STT did).
            self._turn += 1
            user_text = frame.text
            await self._ctx_mgr.ingest_async(ContextItem(
                id=f"u{self._turn}", kind=ItemKind.user_turn, text=user_text,
            ))

            # 2. Route to the top-k functions, off the audio thread.
            routed = self._router.route(user_text)

            # 3. Build the call-phase prompt asynchronously.
            pack = await self._ctx_mgr.build(phase=Phase.call, query=user_text)

            # 4. Hand the LLM a focused frame: budgeted prompt + shortlist.
            await self.push_frame(LLMMessagesFrame(
                messages=[{"role": "user", "content": pack.prompt}],
                functions=[
                    {"name": rid, "description": self._catalog.get(rid).description}
                    for rid in routed.candidate_ids
                ],
            ), direction)

        elif isinstance(frame, FunctionCallResultFrame):
            # 5. The LLM called a function; the result flows back here.
            self._ctx_mgr.ingest_tool_result_sync(
                tool_call_id=f"tc-{self._turn}",
                raw_output=str(frame.result),
                tool_name=frame.function_name,
            )

            # 6. Build the answer-phase prompt asynchronously.
            answer_pack = await self._ctx_mgr.build(
                phase=Phase.answer, query=frame.context,
            )
            await self.push_frame(LLMMessagesFrame(
                messages=[{"role": "user", "content": answer_pack.prompt}],
            ), direction)

        else:
            await self.push_frame(frame, direction)
```

`ContextManager.ingest_async()` and `ContextManager.build()` are both
real async APIs — they don't `asyncio.to_thread` under the hood, they
hand-roll the pipeline so the event loop stays free for the audio
pipeline.

## Wiring into a Pipecat pipeline

```python
import asyncio

from pipecat.pipeline.pipeline import Pipeline
from pipecat.services.openai import OpenAILLMService

from contextweaver.context.manager import ContextManager
from contextweaver.config import ContextBudget
from contextweaver.routing.catalog import Catalog
from contextweaver.types import SelectableItem


def build_catalog() -> Catalog:
    catalog = Catalog()
    for name, desc in [
        ("check_order", "Look up the status of an order"),
        ("update_address", "Update the customer's delivery address"),
        ("schedule_callback", "Schedule a callback at a chosen time"),
        ("send_email", "Send an email to the customer"),
    ]:
        catalog.register(SelectableItem(
            id=name, kind="tool", name=name, description=desc, namespace="support",
        ))
    return catalog


async def main() -> None:
    catalog = build_catalog()
    ctx_mgr = ContextManager(
        # Tight budgets keep TTS responsive in real time.
        budget=ContextBudget(route=300, call=600, interpret=500, answer=1500),
    )

    pipeline = Pipeline([
        # ... (Daily transport, VAD, STT services) ...
        ContextWeaverProcessor(catalog, ctx_mgr),
        OpenAILLMService(model="gpt-4"),
        # ... (TTS service, transport output) ...
    ])

    await pipeline.run()


if __name__ == "__main__":
    asyncio.run(main())
```

## Latency notes

contextweaver's pipeline is pure Python computation — no IO except into
the configured stores — and the in-memory stores are O(1) per append.
End-to-end timings on a single Macbook-class core:

| Stage | Typical |
|---|---|
| `ingest_async()` | < 1 ms |
| `router.route(...)` over 50 tools | 5 – 15 ms |
| `build(phase=Phase.call)` with 20 events | 10 – 30 ms |
| Firewall on a 50 KB tool result | 5 – 10 ms |

Compared to passing the full conversation + every function definition
to the LLM, the wall-clock gain is usually 50 – 200 ms per turn.

## Advanced patterns

- **Per-session episodic memory.** Persist
  `ctx_mgr.event_log.to_dict()` between calls so when the same customer
  calls back, the agent already knows their preferences without
  re-asking.
- **Custom phase budgets.** Tighten `Phase.answer` for fast responses,
  loosen `Phase.interpret` for long tool results.
- **Strict / seeded modes.** Lock determinism for production replays
  by passing a `ProfileConfig` with `mode=Mode.seeded`.
- **Async firewall summarisers.** If your summariser does network IO,
  wrap it as a `Summarizer` protocol implementation and feed it into
  `ContextManager(summarizer=...)`; the pipeline awaits at the right
  boundary.

## Troubleshooting

- **TTS gap mid-turn.** `pack.stats.included_count` is high and
  `build()` is taking > 100 ms — drop the per-phase budget so the
  scoring stage processes fewer candidates, or filter `event_log` to
  the current session window.
- **Function-call loop.** Use `exclude_ids` on the next `router.route()`
  so the model doesn't re-pick the function it just used.
- **Async deadlock.** Always `await ctx_mgr.build(...)` inside Pipecat
  processors — calling `build_sync()` on a running event loop is fine
  in principle (the implementation doesn't block on IO), but mixing
  them inside one processor is confusing. Pick one style per processor.
- **Event log grows unboundedly.** In-memory `EventLog` is intentionally
  append-only. Snapshot + clear at session boundaries; durable backends
  (SQLite, Redis) are tracked under issue
  [#174](https://github.com/dgenio/contextweaver/issues/174).

## See also

- [How contextweaver Fits](interop.md) — boundary, hook points, non-goals
- [Cookbook](cookbook.md) — copy-paste recipes
- [Pipecat docs](https://docs.pipecat.ai/) ·
  [`FrameProcessor` reference](https://docs.pipecat.ai/server/api-reference/frame-processors)
- Tracking issue: [#79](https://github.com/dgenio/contextweaver/issues/79)

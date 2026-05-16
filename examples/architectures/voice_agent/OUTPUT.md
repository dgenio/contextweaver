# Voice agent — captured run

Output of `python examples/architectures/voice_agent/main.py` from a
clean checkout with the optional `pipecat-ai` package not installed.
Deterministic: routing is seed-stable, tool responses are canned, the
firewall is hash-based on artifact content. The customer-service call
walks five turns; the routing scoreboard at the end reports the intent /
shortlist match rate, and the latency scoreboard reports the maximum
answer-prompt token count against the tight 1000-token budget.

> **Note on timings.** The "off-thread" millisecond figures vary between
> machines (and especially between CI runners); they are illustrative,
> not pinned. The smoke test in `tests/test_architectures_voice.py`
> asserts on the structural invariants (intent matches, fact keys, token
> bound) rather than wall-clock numbers.

```
============================================================================
contextweaver -- Voice agent reference architecture
============================================================================
pipecat-ai installed: False
(install 'contextweaver[voice]' to exercise the optional Pipecat hook)
Loaded catalog: 18 tools from catalog.yaml

============================================================================
Turn 1
============================================================================
user:     hi, can you look up order number A-481 for me
routed:   ['orders.lookup', 'account.subscription', 'account.lookup']
chosen:   orders.lookup  (intent='orders.lookup', in shortlist)
route prompt: 1 items / 11 tokens  (0.8 ms off-thread)
answer prompt: included=3  dropped=0  tokens=39  (0.6 ms off-thread)

============================================================================
Turn 2
============================================================================
user:     what is the shipping tracking status for that order
routed:   ['shipping.tracking', 'shipping.update_address', 'orders.status']
chosen:   shipping.tracking  (intent='shipping.tracking', in shortlist)
route prompt: 2 items / 23 tokens  (0.4 ms off-thread)
answer prompt: included=6  dropped=0  tokens=79  (0.6 ms off-thread)

============================================================================
Turn 3
============================================================================
user:     can you change the delivery address to my new home
routed:   ['shipping.update_address', 'shipping.tracking', 'account.lookup']
chosen:   shipping.update_address  (intent='shipping.update_address', in shortlist)
route prompt: 3 items / 35 tokens  (0.4 ms off-thread)
answer prompt: included=9  dropped=0  tokens=118  (0.7 ms off-thread)

============================================================================
Turn 4
============================================================================
user:     when is the next available delivery slot
routed:   ['shipping.delivery_slot', 'shipping.tracking', 'account.lookup']
chosen:   shipping.delivery_slot  (intent='shipping.delivery_slot', in shortlist)
route prompt: 4 items / 45 tokens  (0.3 ms off-thread)
answer prompt: included=12  dropped=0  tokens=159  (0.7 ms off-thread)

============================================================================
Turn 5
============================================================================
user:     schedule a callback for me at 2pm tomorrow
routed:   ['callback.schedule', 'callback.cancel', 'account.lookup']
chosen:   callback.schedule  (intent='callback.schedule', in shortlist)
route prompt: 5 items / 55 tokens  (0.4 ms off-thread)
answer prompt: included=15  dropped=0  tokens=190  (0.9 ms off-thread)

============================================================================
Persisted facts (carry across turns of the call)
============================================================================
  customer.callback = 2026-05-17T14:00 (PT)
  customer.order_id = A-481
  customer.shipping_address = 42 Apple St, Springfield

============================================================================
Latency scoreboard
============================================================================
max answer-prompt tokens: 190 (budget=1000)
Answer-phase builds run via asyncio.to_thread so the audio pipeline stays
unblocked while contextweaver assembles the prompt.

============================================================================
Routing scoreboard
============================================================================
intent in router top-3: 5/5  (100%)
```

## Reading the output

- **Turn 1.** Order-lookup intent routes correctly; the answer prompt
  is 39 tokens — well under the 1000-token voice budget.
- **Turn 2.** Tracking status request routes to `shipping.tracking`.
  Note how the answer prompt grows by ~40 tokens per turn as accumulated
  history compounds; the tight budget would force selective inclusion
  on a longer call.
- **Turn 3.** Address update — a side-effecting tool (`write` tag).
- **Turn 4–5.** Slot lookup and callback scheduling complete the
  customer's needs in a 5-turn call.
- **Persisted facts.** `customer.order_id`, `customer.shipping_address`,
  and `customer.callback` carry across all turns, so the final prompt
  contains the full call state in one place — no re-prompting the
  customer.
- **Latency scoreboard.** All five answer prompts stayed under 200
  tokens, comfortable for the 300 ms TTS budget recommended in the
  [Pipecat integration guide](../../../docs/integration_pipecat.md).
- **Routing scoreboard.** 5/5 intents land in the top-3 shortlist —
  the voice-friendly catalog descriptions tokenize cleanly against
  user phrasing.

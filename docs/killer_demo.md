# The 60-second failure mode

> The fastest way to see why a naive tool-using agent loop breaks down — and
> what contextweaver does about it — in one command, with no API keys and no
> network.

```bash
contextweaver demo --scenario killer
```

(Also available as `python -m contextweaver demo --scenario killer`.)

## The scenario

An internal ops agent with **100 tools** and a running conversation. The
user asks:

> "Find unpaid invoices, check the account notes, and draft a reminder."

A naive loop pays for three things at once:

1. **The tool catalog** — all 100 tool descriptions injected into the route
   prompt.
2. **The conversation history** — every prior turn included raw.
3. **A huge tool result** — the invoice/account dump pasted straight back
   into the answer prompt.

contextweaver narrows each one:

| | Naive | contextweaver | Reduction |
|---|---|---|---|
| Tools in the route prompt | all 100 descriptions (6,326 chars) | 5 ChoiceCards (491 chars) | **92.2%** |
| The huge tool result | raw (14,430 chars) | firewalled summary (60 chars) | **99.6%** |
| The full answer prompt | everything raw (21,332 chars) | compiled (823 chars) | **96.1%** |

Sizes are reported in **characters** (deterministic everywhere). The demo
also prints a token estimate using the active tokeniser.

## What you are seeing

- **Route narrows the catalog.** `Router.route(query)` turns 100 tools into a
  5-card shortlist — the [Tool Router](tool_router.md) at work. The model
  never sees 100 schemas.
- **The firewall externalises the big result.** The ~14 KB invoice dump is
  stored out-of-band as an artifact and replaced with a short summary — the
  [Context Firewall](context_firewall.md). The raw bytes stay addressable.
- **The answer prompt is compiled, not concatenated.** A budget-aware build
  keeps the relevant history plus the summary, instead of dumping everything.

## Where to go next

- The [catalog showcase architecture](architectures/catalog_showcase.md) is
  the same story as a runnable, inspectable script with `BuildStats`.
- The [Showcase](showcase.md) walks the other `demo --scenario` flows
  (`large-catalog`, `huge-tool-output`, `mcp-gateway-full`).
- The [Quickstart](quickstart.md) shows the direct-API version you would
  embed in your own agent loop.

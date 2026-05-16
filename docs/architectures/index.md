# Reference architectures

> End-to-end worked examples sized between the [cookbook](../cookbook.md)
> recipes (≤100 lines each) and a real deployment (thousands of lines). Each
> architecture is runnable, mocked but realistic, and exercises the full
> Context Engine + Routing Engine stack rather than one primitive in
> isolation.

If you are looking for a copy-paste snippet for a single primitive, you want
the [Cookbook](../cookbook.md). If you are looking for a starting template
for a production agent, you are in the right place.

## Available architectures

| Architecture | What it shows | Size |
|---|---|---|
| [Slack ops bot](slack_ops_bot.md) | ~50 internal tools, multi-turn investigations, firewall on log/grep outputs, persistent fact memory across conversations | ~250 lines + YAML catalog |

## How each architecture is structured

Every architecture under `examples/architectures/<name>/` contains:

- `main.py` — the runnable script. Mocked tool implementations, deterministic
  transcript, prints rendered prompts and `BuildStats` to stdout.
- `catalog.yaml` — the tool catalog as a YAML file (loaded via
  `routing.catalog.load_catalog_yaml`).
- `README.md` — explains the setup, lists which contextweaver features are
  load-bearing for this architecture and which are not.
- `OUTPUT.md` — captured output from a known seed so you can read the
  architecture without running it.

The architectures run under `make example` (via the `make architectures`
umbrella target) so they cannot bitrot silently as the library evolves.

## Roadmap

Architectures planned as follow-ups (tracked under issue #198):

- **Code-review bot** — firewall on diff / grep outputs, bounded routing
  across a fixed set of analysis tools, latency-sensitive answer phase.
- **Real-time voice agent** — Pipecat-backed, demonstrates the
  `asyncio.to_thread(mgr.build_sync, …)` pattern and tight answer-phase
  budgets recommended in the [Pipecat guide](../integration_pipecat.md).

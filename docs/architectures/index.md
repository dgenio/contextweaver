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
| [Catalog showcase](catalog_showcase.md) | **Start here.** 65-tool catalog → 5-card shortlist, single-tool schema hydration, firewall on a ~3 KB result — the core value in one linear read | ~250 lines |
| [MCP Context Gateway](mcp_context_gateway.md) | 60-tool MCP-style gateway, 5 `ChoiceCards`, lazy schema hydration, firewall on a 16 KB upstream result, artifact-backed answer-phase prompt | ~240 lines + YAML catalog |
| [Slack ops bot](slack_ops_bot.md) | ~48 internal tools, multi-turn investigations, firewall on log/grep outputs, persistent fact memory across conversations | ~280 lines + YAML catalog |
| [Code-review bot](code_review_bot.md) | ~24 analysis tools, firewall on diff / grep outputs as the load-bearing pattern, tight per-phase budgets for a latency-sensitive review | ~300 lines + YAML catalog |
| [Voice agent](voice_agent.md) | ~18 customer-service tools, `asyncio.to_thread(mgr.build_sync, …)` async pattern, tight budgets for sub-300 ms TTS — canonical worked example for the [Pipecat guide](../integration_pipecat.md) | ~270 lines + YAML catalog |
| [LangGraph agent loop](langgraph_agent_loop.md) | contextweaver **inside** a LangGraph `StateGraph` (route → execute → answer), 36 tools, firewall on a ~21 KB log dump, cross-turn retention — optional framework with a hand-rolled fallback | ~330 lines |
| [Evaluation-artifact profile](eval_artifact_profile.md) | Agent-safe context shaping for offline-evaluation reports — never surfaces `V_hat` without support diagnostics; foregrounds caveats for high-risk artifacts | ~280 lines + JSON fixtures |

## How each architecture is structured

Every architecture under `examples/architectures/<name>/` contains:

- `main.py` — the runnable script. Mocked tool implementations, deterministic
  transcript, prints rendered prompts and `BuildStats` to stdout.
- a tool catalog — usually a `catalog.yaml` loaded via
  `routing.catalog.load_catalog_yaml`, though some (the catalog showcase and
  LangGraph agent loop) generate it deterministically with
  `generate_sample_catalog(...)` plus a few schema-rich inline tools, and the
  evaluation-artifact profile ships JSON fixtures instead.
- `README.md` — explains the setup, lists which contextweaver features are
  load-bearing for this architecture and which are not.
- `OUTPUT.md` — captured output from a known seed so you can read the
  architecture without running it.

The architectures run under `make example` (via the `make architectures`
umbrella target) so they cannot bitrot silently as the library evolves.

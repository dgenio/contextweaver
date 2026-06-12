# contextweaver

> **Context firewall + tool router for MCP and tool-heavy agents.**
> Phase-specific, budget-aware **context engineering** with a deterministic
> core.

![contextweaver architecture overview](assets/hero.svg)

**Minimal core dependencies · deterministic output · Python ≥ 3.10**

> **Context engineering** is the discipline of deciding what goes into a model's
> context window, when, and at what cost — see the
> [canonical framing](https://atlan.com/know/what-is-context-engineering/).

---

contextweaver provides two cooperating engines that solve the context window
problem for tool-using AI agents:

- **Context Engine** — eight-stage pipeline:
  candidates → dependency closure → sensitivity filter → firewall →
  scoring → dedup → selection → rendering. See the
  [Context Firewall](context_firewall.md) page for the load-bearing
  firewall primitive and [Architecture](architecture.md) for the full
  pipeline.
- **Routing Engine** — bounded DAG + beam search over large tool
  catalogs, producing compact LLM-friendly `ChoiceCard`s. See the
  [Tool Router](tool_router.md) page.

Use it when your agent has too many MCP / FastMCP / Python tools, too much
tool-result data, or a long history competing for the same prompt budget. Do
not use it as a replacement for your agent framework, model SDK, memory
database, RAG system, or observability stack.

## Get started

[10-Minute Quickstart](quickstart.md){ .md-button .md-button--primary }
[Daily Driver](daily_driver.md){ .md-button }
[API Reference](reference/){ .md-button }

![Animated demo recording](assets/demo.svg)

## Navigate

| Section | What you'll find |
|---|---|
| [Quickstart](quickstart.md) | Install, first context build, firewall demo, routing demo |
| [Daily Driver](daily_driver.md) | When to use the gateway, client instructions, and the operator debug loop |
| [Security Model](security_model.md) | Gateway data flow, trust boundaries, artifact exposure, and hardening |
| [MCP Client Recipes](recipes/index.md) | Claude Desktop, Claude Code, Copilot, and Cursor setup |
| [Concepts](concepts.md) | Core type glossary: `ContextItem`, `Phase`, `ChoiceGraph`, … |
| [Ecosystem Map](ecosystem.md) | How contextweaver compares with agent frameworks, MCP, memory, RAG, and observability |
| [Adopter Benchmark Report](benchmark_report.md) | Cost, prompt-size, latency, routing-quality, and failure-mode framing |
| [Stability](stability.md) | Alpha/Beta/1.0 readiness and public API stability boundaries |
| [Launch Kit](launch_kit.md) | Reusable public copy, assets, and responsible-claims checklist |
| [Runtime Loop](guide_agent_loop.md) | Four-phase flow diagram and pseudo-code |
| [MCP Integration](integration_mcp.md) | Tool conversion, session loading, firewall with MCP |
| [A2A Integration](integration_a2a.md) | Agent cards and multi-agent sessions |
| [Architecture](architecture.md) | Pipeline details, design rationale, module map |
| [API Reference](reference/) | Auto-generated reference from source docstrings |

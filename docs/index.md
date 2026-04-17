# contextweaver

> Phase-specific, budget-aware context compilation for tool-using AI agents.

**Zero runtime dependencies · deterministic output · Python ≥ 3.10**

---

contextweaver provides two cooperating engines that solve the context window
problem for tool-using AI agents:

- **Context Engine** — eight-stage pipeline: candidates → dependency closure →
  sensitivity filter → firewall → scoring → dedup → selection → rendering.
- **Routing Engine** — bounded DAG + beam search over large tool catalogs,
  producing compact LLM-friendly `ChoiceCards`.

## Get started

[10-Minute Quickstart](quickstart.md){ .md-button .md-button--primary }
[API Reference](reference/){ .md-button }

## Navigate

| Section | What you'll find |
|---|---|
| [Quickstart](quickstart.md) | Install, first context build, firewall demo, routing demo |
| [Concepts](concepts.md) | Core type glossary: `ContextItem`, `Phase`, `ChoiceGraph`, … |
| [Runtime Loop](guide_agent_loop.md) | Four-phase flow diagram and pseudo-code |
| [MCP Integration](integration_mcp.md) | Tool conversion, session loading, firewall with MCP |
| [A2A Integration](integration_a2a.md) | Agent cards and multi-agent sessions |
| [Architecture](architecture.md) | Pipeline details, design rationale, module map |
| [API Reference](reference/) | Auto-generated reference from source docstrings |

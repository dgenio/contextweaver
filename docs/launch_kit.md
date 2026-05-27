# Launch Kit

> Reusable copy and assets for explaining `dgenio/contextweaver` accurately.
> Use this for README snippets, PyPI copy, posts, slides, conference abstracts,
> and ecosystem discussions.

The goal is consistency, not hype. Every claim below should be traceable to a
repo file, a docs page, or a reproducible command.

## One-Sentence Descriptions

| Audience | Copy |
|---|---|
| Technical | contextweaver is a context firewall and tool router for MCP and tool-heavy Python agents. |
| Product / adopter | Keep huge tool catalogs, tool schemas, tool results, and long histories out of the prompt without losing the context your agent needs. |
| OSS / community | A deterministic Python context-compilation layer that sits inside your existing agent loop and works alongside frameworks, MCP servers, memory systems, RAG, and model SDKs. |

## Short Copy Blocks

### 280 Characters

```text
contextweaver is a context firewall + tool router for MCP and tool-heavy agents.
It turns large catalogs into compact ChoiceCards, stores huge tool outputs out
of band, and builds deterministic phase-specific prompts for your existing
agent loop.
```

### LinkedIn Paragraph

```text
contextweaver is a Python context firewall and tool router for MCP and
tool-heavy agents. It does not run your agent or call an LLM. Instead, it sits
inside your existing loop and decides what the model should see this turn:
compact ChoiceCards for routing, artifact-backed summaries for large tool
results, and phase-specific context packs for long histories.
```

### GitHub Repo Blurb

```text
Context firewall + tool router for MCP and tool-heavy AI agents. Deterministic
phase-specific context engineering for large tool catalogs, huge tool results,
and long agent histories.
```

### PyPI Short Description

```text
Context firewall + tool router for MCP and tool-heavy AI agents.
```

## Visual and Demo Assets

| Asset | Use |
|---|---|
| [`docs/assets/hero.svg`](assets/hero.svg) | Architecture overview for README, talks, and posts. |
| [`docs/assets/before_after.svg`](assets/before_after.svg) | Social-ready prompt-budget before/after visual. |
| [`docs/assets/demo.svg`](assets/demo.svg) | Animated demo image for docs pages and posts. |
| [`docs/assets/demo.cast`](assets/demo.cast) | Terminal recording of the default demo. |
| [`docs/assets/casts/large-catalog.cast`](assets/casts/large-catalog.cast) | Large catalog to compact `ChoiceCard`s. |
| [`docs/assets/casts/huge-tool-output.cast`](assets/casts/huge-tool-output.cast) | Huge tool output through the context firewall. |
| [`docs/assets/casts/mcp-gateway-full.cast`](assets/casts/mcp-gateway-full.cast) | Full MCP Context Gateway narrative. |

Do not invent fake dashboards, production metrics, customer logos, or screenshots.
If a visual shows a number, it should come from a committed example, scorecard,
or deterministic demo.

## FAQ Snippets

| Question | Snippet |
|---|---|
| Does it replace LangGraph or CrewAI? | No. Your framework owns control flow; contextweaver owns prompt/context compilation. |
| Is it memory? | Not by itself. It can use memory backends, but its job is deciding what memory and event context enters this turn's prompt. |
| Is it RAG? | No. RAG retrieves documents; contextweaver budgets documents alongside tool results, facts, and history. |
| Does it call the LLM? | No. Core paths are deterministic, LLM-free, and network-free. |
| When should I not use it? | If your tools, outputs, and histories are already tiny, keep the simple prompt path. |
| Is it MCP? | It is not the protocol itself. It can consume MCP-shaped tools/results and can run MCP gateway/proxy patterns. |

## Responsible Claims Checklist

Claims you can make:

- "Reduces prompt tokens by 41.6 %-84.3 % on the six committed benchmark
  scenarios."
- "Routes large catalogs to compact `ChoiceCard` shortlists."
- "Stores large tool outputs out of band and injects summaries."
- "Runs deterministic, LLM-free core context and routing paths."
- "Works alongside MCP, FastMCP, LangGraph, LangChain, LlamaIndex, OpenAI
  Agents SDK, Google ADK, Pipecat, CrewAI, memory systems, and model SDKs."

Claims to avoid:

- "Makes agents 84 % cheaper."
- "Improves answer quality by X %."
- "Solves tool selection at any scale."
- "Replaces MCP, LangGraph, RAG, memory, or observability."
- "Production-ready 1.0 API."
- "Guarantees latency or cost reduction for your workload."

How to cite numbers:

```text
On the committed benchmark scenarios, contextweaver reduces prompt tokens by
41.6 %-84.3 % versus a naive concat-all baseline. Reproduce with:
make benchmark-matrix && make scorecard
```

Link to the [Adopter Benchmark Report](benchmark_report.md) for cost and
latency framing, and to the generated
[benchmark scorecard](https://github.com/dgenio/contextweaver/blob/main/benchmarks/scorecard.md)
for raw numbers.

## Name and SEO Guidance

Use `dgenio/contextweaver` when ambiguity matters. The package/repo should be
described with a subtitle on first mention:

```text
contextweaver - context firewall + tool router for MCP and tool-heavy agents
```

Helpful discovery phrases to use naturally:

| Phrase | Use when |
|---|---|
| context firewall | Describing large tool-result handling. |
| MCP context gateway | Describing gateway/proxy patterns. |
| tool-heavy agents | Describing many-tool catalogs. |
| prompt budgeting | Describing phase-specific token control. |
| tool result firewall | Describing artifact-backed summaries. |
| ChoiceCards | Describing compact routing cards. |

Avoid implying ownership of the broader phrase "context weaver." If someone
asks about similarly named projects, point them to the FAQ entry:
[Is this related to similarly named ContextWeaver projects or research?](faq.md#is-this-related-to-similarly-named-contextweaver-projects-or-research)

## Useful Links

- [Quickstart](quickstart.md)
- [Showcase](showcase.md)
- [Which pattern fits?](which_pattern.md)
- [Ecosystem Map](ecosystem.md)
- [Adopter Benchmark Report](benchmark_report.md)
- [Stability and 1.0 Readiness](stability.md)
- [MCP Context Gateway architecture](architectures/mcp_context_gateway.md)

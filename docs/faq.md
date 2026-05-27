# FAQ

> The most common positioning questions, answered in one paragraph each.
> For deep-technical questions (token budgets, summariser tuning, perf
> overhead, sensitivity-floor configuration) see the [README FAQ](https://github.com/dgenio/contextweaver/blob/main/README.md#faq).

## Is this an agent framework?

**No.** contextweaver does not run the agent loop, decide when to call a
tool, or stop the loop when the task is done. It runs *inside* your agent
loop and produces the prompt that loop hands to the LLM at each phase
(`route`, `call`, `interpret`, `answer`). Use it alongside LangGraph,
CrewAI, Pydantic AI, the OpenAI Agents SDK, smolagents, or your own
hand-rolled loop. See the [Where it fits → Agent frameworks](comparison.md#agent-frameworks)
section for the boundary.

## Is this a memory database?

**Not by itself.** contextweaver has in-session facts and episodes
(`ContextManager.add_fact_sync(...)`, `add_episode_sync(...)`), and the
default `EventLog` / `ArtifactStore` / `FactStore` / `EpisodicStore`
implementations are in-memory. The stores are **protocols** — plug a
persistent backend behind them when you need cross-session memory. A
SQLite-backed `EventLog` and `JsonFileArtifactStore` ship today;
integration with external memory systems (Mem0, Zep, LangMem) is tracked
under [issue #195](https://github.com/dgenio/contextweaver/issues/195).

## Is this RAG?

**No.** RAG retrieves *documents* against a question. contextweaver
compiles *agent-loop events and tools* into a phase-budgeted prompt.
They compose: run RAG to retrieve documents, ingest the retrieved chunks
as `ContextItem(kind=doc_snippet, ...)`, and let contextweaver score
them against the current query under the same budget pressure as
everything else. See [Where it fits → RAG / vector retrieval](comparison.md#rag-vector-retrieval).

## Does it replace LangChain / LlamaIndex / FastMCP / MCP?

**No.** It is positioned to sit beside them, not to replace any of them:

- **LangChain / LangGraph** — your agent control flow. contextweaver runs
  inside it.
- **LlamaIndex** — your retrieval stack. contextweaver ingests its
  retrieved chunks as `doc_snippet` events.
- **FastMCP** — your tool discovery / execution layer. contextweaver
  sits in front, bounding the catalog the agent sees and firewalling
  large results. See [`examples/fastmcp_adapter_demo.py`](https://github.com/dgenio/contextweaver/blob/main/examples/fastmcp_adapter_demo.py).
- **MCP** — the wire protocol. `contextweaver.adapters.ProxyRuntime`
  implements the gateway shape on top of the MCP wire format; it does
  not replace MCP, it consumes and produces MCP-shaped messages. See
  the [MCP integration guide](integration_mcp.md) and the
  [Gateway specification](gateway_spec.md).

## Does it call an LLM?

**No.** Every contextweaver core path is LLM-free, network-free, and
deterministic. The Context Engine pipeline runs in pure Python (stdlib
+ minimal core deps) and produces a `ContextPack.prompt` you hand to
your model SDK. The default summariser uses heuristics rather than an
LLM; an optional LLM-backed summariser is tracked under
[issue #26](https://github.com/dgenio/contextweaver/issues/26).

## Can it work with MCP?

**Yes — directly.** `ContextManager.ingest_mcp_result(...)` accepts the
canonical MCP tool-result wire shape (`{"content": [...], "isError": ...}`),
parses it, stores binary content in the artifact store, runs the firewall
on large text, and appends a properly-typed `ContextItem` to the event
log. The [MCP integration guide](integration_mcp.md) walks through
the full pattern; the [MCP Context Gateway architecture](architectures/mcp_context_gateway.md)
shows it end-to-end against a 60-tool catalog.

For a live gateway over stdio, see
`src/contextweaver/adapters/mcp_gateway_server.py` and
[`examples/mcp_gateway_demo.py`](https://github.com/dgenio/contextweaver/blob/main/examples/mcp_gateway_demo.py).

## What happens with very large tool catalogs?

The Routing Engine emits a small `ChoiceCard` shortlist regardless of
catalog size — the model never sees the full catalog at once. **What
does change with size is recall:** at `catalog_size = 50` the default
lexical scorer puts the gold-set tool in the top-5 about 56 % of the
time; at `catalog_size = 1000` that drops to about 15 %. This is the
well-known lexical-retrieval ceiling on large tool catalogs.

The honest framing: contextweaver makes the scorer **pluggable**
(`Router(scorer_backend=...)` with `tfidf`, `bm25`, `fuzzy` today; an
embedding-ANN backend tracked under [issue #8](https://github.com/dgenio/contextweaver/issues/8)
and surfaced in the scorecard via [issue #266](https://github.com/dgenio/contextweaver/issues/266)).
It does *not* claim the default scorer is sufficient at scale. Read
[the scorecard's Known limits section](benchmarks.md#known-limits-and-honest-framing)
before deciding what backend to use for your catalog size.

## How do I measure whether it helps?

Three options, in order of effort:

1. **Run the committed benchmark scorecard.** Reports prompt-token
   counts, recall, routing latency across four scenarios and three
   catalog sizes. Honest framing for each metric is in
   [Benchmarks → How to read these numbers](benchmarks.md#how-to-read-these-numbers).

   ```bash
   make benchmark-matrix && make scorecard
   ```

2. **Run the side-by-side comparison on your own data.** [`examples/before_after.py`](https://github.com/dgenio/contextweaver/blob/main/examples/before_after.py)
   tokenises a "naïve concat" prompt and a contextweaver-built prompt
   for the same event log; the delta is your real reduction number.
   Substitute your own JSONL event log for the default fixture.

3. **Wire OpenTelemetry and measure end-to-end.** [Observability](integration_otel.md)
   describes the GenAI semantic-convention attributes contextweaver
   emits. Compare model latency and `gen_ai.client.token.usage` between
   your existing prompt-assembly path and the contextweaver path.

## What are the limitations?

- **Routing-recall ceiling at scale** with the default lexical scorers
  (see [What happens with very large tool catalogs?](#what-happens-with-very-large-tool-catalogs)).
- **No cross-session memory backend by default** — bring your own
  via the store protocols. SQLite-backed `EventLog` /
  `JsonFileArtifactStore` ship; broader integrations
  ([#195](https://github.com/dgenio/contextweaver/issues/195)) are tracked.
- **No end-to-end cost / latency benchmark.** The scorecard reports
  Context-Engine and Routing-Engine metrics, not real model latency or
  cost. An optional gated `make benchmark-e2e` is tracked under
  [issue #269](https://github.com/dgenio/contextweaver/issues/269).
- **No first-class LLM summariser.** The default summariser is
  rule-based; an LLM-backed plugin is tracked under
  [issue #26](https://github.com/dgenio/contextweaver/issues/26).
- **Token estimator is `CharDivFourEstimator`** in the benchmark, not
  real `tiktoken.cl100k_base`. Production paths can pass a real
  `tiktoken` estimator; the benchmark uses chars-÷-4 to stay
  network-independent. The scorecard includes a token-estimator parity
  section when `cl100k_base` is available, and the
  [troubleshooting guide](troubleshooting.md#offline-air-gapped-tiktoken-warning)
  explains the offline fallback warning.

## Is this related to similarly named ContextWeaver projects or research?

Not intentionally. This repository is the Python package
[`dgenio/contextweaver`](https://github.com/dgenio/contextweaver), positioned
as a context firewall and tool router for MCP and tool-heavy agents. The name
"context weaver" is descriptive enough that other projects or papers may use
similar phrasing. When citing this project publicly, use `dgenio/contextweaver`
or `contextweaver` on PyPI and include the subtitle "context firewall + tool
router for MCP and tool-heavy agents" to avoid confusion.

## See also

- [Showcase](showcase.md) — four runnable demos in under a minute each.
- [Where it fits](comparison.md) — full-page version of the per-category
  positioning above.
- [Which pattern fits?](which_pattern.md) — symptom-driven routing into
  the right contextweaver primitive.
- [Quickstart](quickstart.md) — ten-minute walkthrough with code.
- [Benchmarks](benchmarks.md) — measured numbers with honest framing.
- [README FAQ](https://github.com/dgenio/contextweaver/blob/main/README.md#faq) —
  deep-technical questions (budgets, summariser tuning, perf overhead,
  sensitivity-floor configuration).

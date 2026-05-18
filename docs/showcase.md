# Showcase

> Four short, deterministic, network-free runs that exercise the load-bearing
> parts of contextweaver in under a minute each. Every demo here is committed
> code — copy the command, paste it into your terminal, read the output.

Each demo answers one question:

| # | Demo | Question it answers |
|---|---|---|
| 1 | [Large catalog → compact ChoiceCards](#1-large-catalog-compact-choicecards) | What happens when my agent has hundreds of tools? |
| 2 | [Huge tool output → context firewall](#2-huge-tool-output-context-firewall) | What happens when one tool returns 16 KB of rows? |
| 3 | [Long conversation → relevant dependency chain](#3-long-conversation-relevant-dependency-chain) | What happens when my conversation gets to 50+ turns? |
| 4 | [MCP Context Gateway end-to-end](#4-mcp-context-gateway-end-to-end) | What does the whole launch narrative look like in one transcript? |

Prerequisites for every demo:

```bash
pip install contextweaver
```

No API keys, no `OPENAI_API_KEY`, no Docker, no network access required.

---

## 1. Large catalog → compact ChoiceCards

**Problem:** Your agent has 1,000 tools. Dumping every tool schema into the
prompt would cost ~10 KB per tool. The model also picks worse when buried in
options.

**What contextweaver does:** routes the user query against the catalog and
emits 5 compact `ChoiceCard`s — tool name, description, tags, score. No
schemas in the prompt. Schemas are hydrated lazily only for the tool the
agent picks.

**Run it:**

```bash
contextweaver demo --scenario large-catalog
```

**Key output:**

```text
Catalog size:           1000 tools across 8 namespaces
Routing graph:          1057 nodes, depth=3
Beam width / top_k:     3 / 5

Query: 'create a github issue for an incident'
Cards exposed to model: 5 of 1000
Selected candidate IDs: ['analytics.events.track', 'analytics.metrics.define',
                         'analytics.events.track.v2', 'analytics.events.track.v3',
                         'admin.audit.export']

Card text the model sees (541 chars — note: NO full schemas):
[1/5] analytics.events.track (tool) — Track a custom analytics event …
```

**What to look for:**

- **`5 of 1000`** — the agent sees ChoiceCards for five tools, never the
  other 995.
- **`541 chars`** — the entire route-phase prompt fragment for those 5
  cards is ~540 chars. A naïve "dump all schemas" approach for 1000 tools
  would be on the order of 10 MB.
- **Deterministic order** — repeated runs produce byte-identical output.

**Where to dig further:**

- [Routing concepts](concepts.md) — what `ChoiceCard`, `Catalog`, `Router`,
  beam search actually mean.
- [Cookbook: routing recipes](cookbook.md) — copy-paste snippets for
  custom catalogs.
- [Benchmark scorecard](https://github.com/dgenio/contextweaver/blob/main/benchmarks/scorecard.md) —
  recall / precision / latency at 50, 83, 1000 tools, with the honest
  framing about lexical-retrieval ceilings at scale ([Known limits](benchmarks.md#known-limits-and-honest-framing)).

---

## 2. Huge tool output → context firewall

**Problem:** One tool call returns a 16 KB BigQuery rowset. If you inject
that raw into the answer prompt, you blow the budget and the model loses
focus.

**What contextweaver does:** the firewall intercepts every `tool_result`
item. The raw bytes go to the artifact store. The prompt sees only a
compact summary plus an artifact handle the agent can drill into on
demand.

**Run it:**

```bash
contextweaver demo --scenario huge-tool-output
```

**Key output:**

```text
Raw tool output:   9689 chars (120 rows)

--- After context firewall ---
What enters the prompt (item.text): 50 chars
Prompt-side summary:
  status: ok
  rows_returned: 120
  execution_time_ms: 248

Artifact ref:      ArtifactRef(handle='artifact:tr-bigquery', size_bytes=9689, ...)
Token savings vs raw: 99.5%
```

**What to look for:**

- **`9689 chars` → `50 chars`** — the raw rowset stayed out of the prompt
  entirely; only a header summary went in.
- **`Artifact ref:`** — the full bytes are still addressable. An agent can
  call `tool_view(handle, selector=...)` to fetch a slice when it actually
  needs one.
- **`Extracted facts`** — the firewall pulled structured fields (`status:
  ok`, `rows_returned: 120`) out of the raw text so the answer phase can
  reason about them without re-reading the rowset.

**Where to dig further:**

- [Cookbook §4: firewall + drilldown](cookbook.md) — the canonical
  recipe.
- [Concepts → ContextItem, ArtifactRef](concepts.md) — the underlying
  types.
- [`examples/tool_wrapping.py`](https://github.com/dgenio/contextweaver/blob/main/examples/tool_wrapping.py) —
  the minimal direct-API version of this scenario.

---

## 3. Long conversation → relevant dependency chain

**Problem:** Your agent has been talking to a user for 50 turns. Most of
those turns aren't relevant to the current question. But if you naively
drop old turns, you lose the *tool call* that produced the *tool result*
the agent is now reasoning about — a "dependency closure" violation that
makes the model hallucinate.

**What contextweaver does:** scores conversation events by relevance to
the current query and packs the highest-scoring ones into the answer-phase
budget. If a tool result is included, its parent tool call is included
automatically via the `parent_id` chain — dependency closure is enforced
as a pipeline stage, not as a hope.

**Run it:**

```bash
contextweaver demo
```

…or for the long-conversation scenario specifically:

```bash
python examples/full_agent_loop.py
```

**Key output (from `contextweaver demo`):**

```text
[4/5] Built context pack: phase=answer
      Candidates: 13, Included: 9
      Dedup removed: 0, Closures: 2
      Token breakdown: {'user_turn': 28, 'tool_call': 22, 'tool_result': 64, ...}

[5/5] Prompt preview (320 chars total):
[USER]
How many open invoices do we have?

[TOOL CALL]
invoices.search(status='open')

[TOOL RESULT [artifact:artifact:tr1]]
summary: 2 open invoices, total $8,200
```

**What to look for:**

- **`Closures: 2`** — two events were pulled into the prompt because they
  were parents of higher-scoring events. Without this, you'd see a
  tool result with no tool call to explain it.
- **`Included: 9` of `Candidates: 13`** — four events were dropped under
  budget pressure. The dropped events are the ones with the lowest score
  for *this specific query*; a different query would keep a different
  subset.
- **`[FACTS]` block** — durable facts written via
  `ContextManager.add_fact_sync(...)` survive across turns and land in
  the answer prompt even when their originating events get budget-dropped.

**Where to dig further:**

- [Concepts → Phases and dependency closure](concepts.md).
- [Architecture → 8-stage pipeline](architecture.md) — `generate_candidates`
  → `dependency_closure` → `sensitivity_filter` → `apply_firewall` →
  `score_candidates` → `deduplicate_candidates` → `select_and_pack` →
  `render_context`.
- [`examples/full_agent_loop.py`](https://github.com/dgenio/contextweaver/blob/main/examples/full_agent_loop.py)
  for the multi-turn case.

---

## 4. MCP Context Gateway end-to-end

**Problem:** All three of the above patterns happen in one realistic agent
turn against an MCP-style tool gateway. How do they compose?

**What contextweaver does:** [`examples/architectures/mcp_context_gateway/`](https://github.com/dgenio/contextweaver/tree/main/examples/architectures/mcp_context_gateway)
is the launch reference architecture — a single deterministic transcript
walking a 60-tool catalog, a routing query, lazy schema hydration, a 16 KB
mocked upstream result, the firewall, and a 120-token final prompt.

**Run it:**

```bash
python examples/architectures/mcp_context_gateway/main.py
```

**Key metrics block (captured at the end of every run):**

```text
catalog_tools           = 60
exposed_choice_cards    = 5
hydrated_schema_chars   = 854   (selected tool only)
raw_result_chars        = 16,507
injected_summary_chars  = 194
firewall_reduction_pct  = 98.8 %
artifact_handle         = artifact:result:tc1
final_prompt_tokens     = 120
final_prompt_chars      = 645
```

**What to look for:**

- **All four primitives in one transcript** — catalog routing, lazy
  schema hydration, the firewall on a large result, dependency-preserving
  answer prompt.
- **`final_prompt_chars = 645`** — the entire answer-phase prompt is 645
  chars. A naïve "dump all 60 schemas + raw rowset" prompt for the same
  scenario would be ~50 KB.
- **`98.8 %`** is for this *one rowset shape*. Read the
  [honest framing in the benchmark docs](benchmarks.md#single-call-gateway-scenario) —
  your raw tool outputs will be different sizes and per-call reduction
  will vary.

**Where to dig further:**

- [MCP Context Gateway architecture page](architectures/mcp_context_gateway.md)
  — full walkthrough of how this maps to a real MCP runtime.
- [Captured run output](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/mcp_context_gateway/OUTPUT.md)
  — the full transcript with every numbered step.
- [MCP integration guide](integration_mcp.md) — how to wire the same
  primitives to a live `mcp.server.Server` over stdio.
- [Gateway specification](gateway_spec.md) — the protocol-level rules the
  architecture follows.

---

## All four demos together

```bash
# Friendly walkthrough on a small event log.
contextweaver demo

# 1,000-tool catalog → 5 compact ChoiceCards.
contextweaver demo --scenario large-catalog

# 16 KB tool result → firewall, summary, artifact.
contextweaver demo --scenario huge-tool-output

# MCP gateway meta-tools end-to-end (stubbed upstream, no network).
contextweaver demo --scenario mcp-gateway

# Full launch reference architecture run.
python examples/architectures/mcp_context_gateway/main.py
```

Each is deterministic — repeated runs produce byte-identical output. None
of them call an LLM or hit the network. See the [quickstart](quickstart.md)
for the next step after running the demos.

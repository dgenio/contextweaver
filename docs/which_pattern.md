# Which contextweaver pattern fits my use case?

> A short, symptom-based decision tree. Each branch ends in **one** concrete
> next step. If your problem isn't listed, jump to the
> [10-minute quickstart](quickstart.md).

contextweaver is built from composable pieces — you do **not** have to adopt
all of it. Pick the smallest piece that fixes your symptom, then grow into
the rest when you need it.

The three building blocks:

| Piece | What it does | When you need it |
|---|---|---|
| **Routing engine** | Bounded shortlist of tools from a large catalog | LLM picks badly with many tools |
| **Context firewall** | Keeps huge tool outputs out of the prompt; raw bytes go to an artifact store | One tool returns much more than fits the budget |
| **Full pipeline** | All eight context stages: candidates → closure → sensitivity → firewall → score → dedup → select → render | Long conversations + tool history that overflow the window |

If you're starting a brand-new agent, walk the [10-minute quickstart](quickstart.md)
first, then come back here when a specific symptom shows up.

---

## "I'm hitting context-window limits on long conversations."

**Start with the full pipeline.** Use [`ContextManager.build()`](architecture.md)
(or `build_sync()`) with phase-specific token budgets. The
`select_and_pack` stage drops low-scoring items first, so a 10K-turn history
collapses to a budgeted prompt deterministically.

Concrete next step: walk the [10-minute quickstart](quickstart.md) end-to-end,
then read the [`Phase`](concepts.md) section to tune per-phase budgets.

```python
from contextweaver.context.manager import ContextManager
from contextweaver.types import Phase

mgr = ContextManager()
# … ingest events …
pack = mgr.build_sync(phase=Phase.answer, query="user question")
print(pack.prompt)
print(pack.stats)  # what was kept, dropped, deduplicated
```

---

## "My agent has 50+ tools and the LLM picks badly."

**Start with routing-only.** Build a [`Catalog`](architecture.md), run
[`Router.route()`](architecture.md), feed the shortlist into your existing
agent. You don't need the rest of the library.

Concrete next step: skim
[`examples/routing_demo.py`](https://github.com/dgenio/contextweaver/blob/main/examples/routing_demo.py),
then read [Routing](architecture.md#routing-engine) for the four-stage pipeline.

```python
from contextweaver.routing.catalog import Catalog
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder

catalog = Catalog()
# … register SelectableItem objects …
graph = TreeBuilder(max_children=8).build(catalog.all())
router = Router(graph, items=catalog.all(), top_k=5)
result = router.route("send a reminder email about unpaid invoices")
print(result.candidate_ids)
```

---

## "One tool returns 100 KB of JSON and blows my budget."

**Start with firewall-only.** Ingest results through
[`ContextManager.ingest_tool_result_sync()`](architecture.md); the firewall
stores raw bytes in the `ArtifactStore` and injects a compact summary into the
event log. Drill into the artifact when the agent asks for specifics.

Concrete next step: copy
[Cookbook recipe 4 — Firewall + drilldown](cookbook.md#4-firewall--drilldown-for-large-tool-outputs)
verbatim and replace the mocked payload with your tool.

```python
mgr.ingest_tool_result_sync(
    tool_call_id="tc1",
    raw_output=huge_json,
    tool_name="logs.fetch",
    firewall_threshold=2000,
)
# Later, drill into the artifact for a targeted slice:
mgr.drilldown_sync(handle=item.artifact_ref.handle,
                   selector={"type": "json_keys", "keys": ["errors"]},
                   inject=True, parent_id="tc1")
```

---

## "I'm running a real-time voice or chat agent and latency matters."

**Use the full pipeline, but run `build_sync()` off the event loop and tighten
the answer-phase budget.** contextweaver's pipeline is sync-internally but
async-callable; the recommended pattern is `asyncio.to_thread(mgr.build_sync, …)`
so the 10–50 ms context build doesn't block your turn-taking loop.

Concrete next step: read the [Pipecat guide](integration_pipecat.md) for the
canonical pattern; tighten `ContextBudget(answer=…)` until your TTFT target is
met.

```python
pack = await asyncio.to_thread(mgr.build_sync, phase=Phase.answer, query=q)
```

---

## "I already use MCP / FastMCP servers and just need a smarter `tools/list`."

**Use the adapter + routing-only.** Convert MCP tool definitions to
`SelectableItem` (`adapters.mcp.mcp_tool_to_selectable` / `adapters.fastmcp.fastmcp_tools_to_catalog`)
and serve a top-k shortlist instead of the full server tool list.

Concrete next step: pick the recipe that matches your runtime —
[MCP integration guide](integration_mcp.md) for raw MCP servers,
[FastMCP cookbook recipe](cookbook.md#1-fastmcp--contextweaver-routing) for
composed FastMCP servers.

---

## "I already have an OpenAI / Anthropic / Gemini agent and want to drop contextweaver in."

**Use the provider-message adapters.** They take your existing chat history
(plain dicts, no SDK import required) and produce a populated
`ContextManager` in one call — five lines including imports.

Concrete next step:
[Adopting from an existing chat history](quickstart.md#adopting-from-an-existing-chat-history-5-line-drop-in)
shows the OpenAI, Anthropic, and Gemini variants side-by-side. The adapters
ship inverse functions so you can hand the rebuilt array back to the
provider SDK without changing the rest of your loop.

---

## "I need to wrap my existing Python callables — no protocol."

**Use the bring-your-own-tools recipe.** It's the canonical adapter shape:
register each callable as a `SelectableItem`, route over them, ingest the
result through the firewall.

Concrete next step: copy
[Cookbook recipe 3 — Bring-your-own-tools](cookbook.md#3-bring-your-own-tools)
and adapt. This is also the right starting point for a fully custom runtime.

---

## "I want a realistic end-to-end template, not a toy."

**Skim a production reference architecture.** The `examples/architectures/`
directory ships full worked examples that exercise the routing engine, the
firewall, persistent facts, and multi-turn investigation patterns together.

Concrete next step: read
[Slack ops bot](architectures/slack_ops_bot.md) — ~50 internal tools, mocked
log/grep/deploy/oncall calls, multi-turn investigations, persistent facts.
Run it with `python examples/architectures/slack_ops_bot/main.py`.

---

## Still not sure?

Read the [How contextweaver Fits](interop.md) positioning page — it answers
"is this for me?" in five lines, then maps every component to the runtimes it
hooks into. From there:

- [Architecture overview](architecture.md) — the two engines and eight stages
- [Cookbook](cookbook.md) — four copy-paste recipes for common shapes
- [Troubleshooting](troubleshooting.md) — 10 common issues with fixes
- [Examples](https://github.com/dgenio/contextweaver/tree/main/examples) — all runnable scripts

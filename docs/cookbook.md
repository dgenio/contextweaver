# Cookbook

> Copy-paste recipes for the most common integration patterns. Each recipe
> is runnable end-to-end, uses only contextweaver core (no framework SDK
> required), and is exercised by `make example` so it does not bitrot.

The recipes:

1. [FastMCP + contextweaver routing](#1-fastmcp-contextweaver-routing)
2. [A2A multi-agent session](#2-a2a-multi-agent-session)
3. [Bring-your-own-tools](#3-bring-your-own-tools)
4. [Firewall + drilldown for large tool outputs](#4-firewall-drilldown-for-large-tool-outputs)
5. [CrewAI routing — bounded tool shortlists for crews](#5-crewai-routing-bounded-tool-shortlists-for-crews)
6. [External memory backend — Mem0 / Zep / LangMem](#6-external-memory-backend)
7. [Post-generation safety gate for agent-generated diffs](#7-post-generation-safety-gate-for-agent-generated-diffs)

If you are evaluating where contextweaver fits in your runtime, start with
the [How contextweaver Fits](interop.md) page first; come back here for
working code.

Looking for something larger than a recipe? [Reference architectures](architectures/index.md)
ship end-to-end worked examples (50-tool catalog, multi-turn investigations,
persistent facts) that exercise the full stack rather than one primitive at a
time.

---

## 1. FastMCP + contextweaver routing

**Goal.** Load a tool list from a FastMCP server, convert it into a
contextweaver `Catalog`, build a bounded-choice routing graph, and route
user queries to the most relevant tool.

**Use this when:** you front N upstream MCP servers via FastMCP composition
and you need an LLM-friendly shortlist instead of dumping every tool into
the prompt.

The repo already ships a runnable demo:

```bash
python examples/fastmcp_adapter_demo.py
```

Key pieces (see [`examples/fastmcp_adapter_demo.py`](https://github.com/dgenio/contextweaver/blob/main/examples/fastmcp_adapter_demo.py) for the full version):

```python
from contextweaver.adapters.fastmcp import fastmcp_tools_to_catalog
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder

# Tool list as it would arrive from a composed FastMCP server.  Use
# load_fastmcp_catalog() instead when you want to discover from a live
# server (requires `pip install 'contextweaver[fastmcp]'`).
FASTMCP_TOOLS = [
    {"name": "github_search_repos", "description": "Search GitHub repositories",
     "annotations": {"readOnlyHint": True}},
    {"name": "github_create_issue", "description": "Open a new GitHub issue"},
    {"name": "slack_send_message", "description": "Send a message to Slack"},
    {"name": "db_query", "description": "Run a read-only SQL query",
     "annotations": {"readOnlyHint": True}},
]

catalog = fastmcp_tools_to_catalog(FASTMCP_TOOLS)
graph = TreeBuilder(max_children=8).build(catalog.all())
router = Router(graph, items=catalog.all(), top_k=2)

result = router.route("send a reminder to the platform channel")
print(result.candidate_ids)   # → ['fastmcp:slack_send_message', ...]
```

The adapter preserves MCP annotations (`readOnlyHint`, `destructiveHint`,
`costHint`) as `SelectableItem.side_effects` / `cost_hint` / tags, so the
router can score them naturally — and so you can apply
negative routing (`Router.route(..., exclude_ids=..., exclude_tags=...)`)
and catalog-level toolset gating without extra plumbing. See the [FastMCP adapter source](https://github.com/dgenio/contextweaver/blob/main/src/contextweaver/adapters/fastmcp.py)
for the full mapping table.

> Annotations are server-declared hints, not security controls. See the
> [MCP guide's security note](integration_mcp.md#security-considerations).

---

## 2. A2A multi-agent session

**Goal.** Import agent cards from A2A peers, treat each peer as a routable
"agent" `SelectableItem`, and replay a multi-agent session through a single
`ContextManager`.

**Use this when:** you have an orchestrator that delegates work to
specialised peer agents and you need unified, budget-aware context across
the handoffs.

The repo ships a runnable demo:

```bash
python examples/a2a_adapter_demo.py
```

The shape of the adapter:

```python
from contextweaver.adapters.a2a import (
    a2a_agent_to_selectable,
    load_a2a_session_jsonl,
)
from contextweaver.context.manager import ContextManager
from contextweaver.types import ItemKind, Phase

AGENT_CARD = {
    "name": "DataAgent",
    "description": "Retrieves and aggregates warehouse data",
    "skills": [
        {"id": "sql_query",   "name": "SQL Query",  "description": "Run SQL"},
        {"id": "aggregate",   "name": "Aggregate",  "description": "Group + sum"},
    ],
}
agent = a2a_agent_to_selectable(AGENT_CARD)
# agent.kind == "agent"; route over a Catalog containing many such peers.

mgr = ContextManager()
for item in load_a2a_session_jsonl("examples/data/a2a_session.jsonl"):
    if item.kind == ItemKind.tool_result and len(item.text) > 2000:
        mgr.ingest_tool_result_sync(
            tool_call_id=item.parent_id or item.id,
            raw_output=item.text,
            tool_name="a2a_peer",
        )
    else:
        mgr.ingest_sync(item)

pack = mgr.build_sync(phase=Phase.answer, query="Q4 report")
print(pack.prompt)
```

See [A2A Integration](integration_a2a.md) for the full reference, including
the session JSONL format used above.

---

## 3. Bring-your-own-tools

**Goal.** Wrap plain Python callables as `SelectableItem`s, route over
them, and feed the shortlist into your own agent loop. No protocol
adapter, no framework SDK.

**Use this when:** you are not using MCP / A2A / FastMCP, or you are
prototyping. Also the canonical starting point for a custom runtime.

Recipe script: [`examples/cookbook/byot_recipe.py`](https://github.com/dgenio/contextweaver/blob/main/examples/cookbook/byot_recipe.py).

```python
from contextweaver.context.manager import ContextManager
from contextweaver.routing.catalog import Catalog
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import ContextItem, ItemKind, Phase, SelectableItem


def send_email(to: str, subject: str, body: str) -> str:
    """Send an email to *to* with *subject* and *body*."""
    return f"send_email(to={to!r}) → ok"

# 1. Register each callable as a SelectableItem.
catalog = Catalog()
catalog.register(SelectableItem(
    id="send_email",
    kind="tool",
    name="send_email",
    description=(send_email.__doc__ or "").strip().splitlines()[0],
    namespace="email",
    tags=["email"],
))
# (register your other tools the same way)

# 2. Build the routing graph + router.
graph = TreeBuilder(max_children=4).build(catalog.all())
router = Router(graph, items=catalog.all(), top_k=2)

# 3. Route the user query → the LLM sees a shortlist, not the catalog.
result = router.route("send a follow-up email to alice@example.com")
chosen = result.candidate_ids[0]   # your runtime calls the tool

# 4. Feed the result back through the firewall so future builds see a
# summary, not the raw bytes.
mgr = ContextManager()
mgr.ingest_sync(ContextItem(id="u1", kind=ItemKind.user_turn, text="..."))
mgr.ingest_sync(ContextItem(id="tc1", kind=ItemKind.tool_call,
                            text=f"{chosen}(...)", parent_id="u1"))
mgr.ingest_tool_result_sync(
    tool_call_id="tc1",
    raw_output=send_email("alice@example.com", "FYI", "..."),
    tool_name=chosen,
)
pack = mgr.build_sync(phase=Phase.answer, query="...")
# Send pack.prompt to whichever LLM you like.
```

This pattern is the canonical adapter shape — `adapters.mcp`,
`adapters.a2a`, and `adapters.fastmcp` all just emit `SelectableItem` /
`ContextItem` / `ResultEnvelope` instances and the rest of the pipeline
treats them identically.

---

## 4. Firewall + drilldown for large tool outputs

**Goal.** Keep huge tool payloads (logs, dumps, multi-MB JSON) out of the
prompt while still letting the agent inspect the parts it needs.

**Use this when:** any single tool you wire up can return more than a few
KB of text. The firewall is on by default; the drilldown API is how the
agent asks for specifics.

Recipe script: [`examples/cookbook/firewall_drilldown_recipe.py`](https://github.com/dgenio/contextweaver/blob/main/examples/cookbook/firewall_drilldown_recipe.py).

```python
import json

from contextweaver.context.manager import ContextManager
from contextweaver.types import ContextItem, ItemKind, Phase

LARGE = json.dumps({"events": [{"i": i} for i in range(200)]})

mgr = ContextManager()
mgr.ingest_sync(ContextItem(id="u1", kind=ItemKind.user_turn, text="logs?"))
mgr.ingest_sync(ContextItem(id="tc1", kind=ItemKind.tool_call,
                            text="logs.fetch(...)", parent_id="u1"))
item, env = mgr.ingest_tool_result_sync(
    tool_call_id="tc1",
    raw_output=LARGE,
    tool_name="logs.fetch",
    firewall_threshold=2000,
)
# item.text is now a compact summary; the raw bytes live in
# mgr.artifact_store under item.artifact_ref.handle.

# Pull a targeted slice and re-inject it as a new tool_result so subsequent
# build() calls can see it without re-fetching from the artifact.
mgr.drilldown_sync(
    handle=item.artifact_ref.handle,
    selector={"type": "json_keys", "keys": ["errors", "total_events"]},
    inject=True,
    parent_id="tc1",
)

pack = mgr.build_sync(phase=Phase.answer, query="errors in the last hour")
# pack.prompt now contains the summary AND the targeted drilldown slice.
```

### Drilldown selector types

| Selector | Example | Returns |
|---|---|---|
| `head` | `{"type": "head", "chars": 600}` | First *N* chars of the artifact |
| `lines` | `{"type": "lines", "start": 0, "end": 25}` | Line range *S..E* (exclusive end) |
| `json_keys` | `{"type": "json_keys", "keys": ["errors"]}` | A JSON object with just the requested top-level keys |
| `rows` | `{"type": "rows", "start": 0, "end": 50}` | Row range for CSV/TSV text |

### Ordering caveat

Drill in *before* the next `build()` if you want the **raw** bytes — each
`build()` re-runs the firewall stage over every `tool_result` candidate
and re-stores the current `item.text` (already a summary, post-firewall)
under the same artifact handle. The injected drilldown `ContextItem`
survives because it lives in the event log, not in the artifact store.
This is tracked as a known sharp edge — see the recipe's module docstring.

---

## 5. CrewAI routing — bounded tool shortlists for crews

**Goal.** Take a crew's full tool registry, convert it into a
contextweaver `Catalog`, route each task to a top-`k` shortlist, and
hand only that shortlist to the agent's `BaseTool` list — so the LLM's
system prompt never carries every tool's schema.

**Use this when:** your crew has 10+ tools and you see prompts ballooning
past 4 K tokens before any reasoning happens.

The repo already ships a runnable demo:

```bash
python examples/crewai_adapter_demo.py
```

Full walkthrough (including the firewall-wrap pattern for `_run`):
[CrewAI Integration](integration_crewai.md).

---

## 6. External memory backend

**Goal.** Plug an existing [Mem0](https://docs.mem0.ai/) deployment as
the backing `EpisodicStore` / `FactStore` so contextweaver compiles
per-turn prompts over memory you've already invested in — instead of
the bundled in-memory or SQLite stores.

**Use this when:** your agent already maintains long-lived memory in
Mem0 (or [Zep](https://www.getzep.com/) / [LangMem](https://langchain-ai.github.io/langmem/)
when those adapters land) and you don't want a second persistence
layer to keep in sync.

```python
from mem0 import Memory

from contextweaver.extras.memory.mem0 import Mem0EpisodicStore, Mem0FactStore
from contextweaver.context.manager import ContextManager
from contextweaver.store import StoreBundle

memory = Memory()  # your existing mem0 client
bundle = StoreBundle(
    episodic_store=Mem0EpisodicStore(memory, user_id="agent:support-bot"),
    fact_store=Mem0FactStore(memory, user_id="agent:support-bot"),
)
ctx_mgr = ContextManager(stores=bundle)
```

Full walkthrough, decision matrix, and the Zep / LangMem follow-up
status: [External Memory Backends](integration_memory.md).

---

## 7. Post-generation safety gate for agent-generated diffs

**Goal.** Pair contextweaver (which decides *what context the agent acts on*)
with a deterministic safety gate (which checks *what the agent produced*) so an
agent that edits files cannot quietly ship an unreviewed change.

**Use this when:** your agent generates or edits code and you want a
fail-closed check on the resulting diff before it reaches review or merge —
without putting the scanner inside the LLM's context path.

These are adjacent stages of the same loop, not competitors:

1. contextweaver compiles phase-specific context (route → call → interpret →
   answer) so the agent acts on the right, bounded information.
2. The agent generates an artifact — a diff, a file edit, a patch.
3. A deterministic gate inspects that artifact *outside* the model context.
   This recipe uses [VibeGuard](https://github.com/dgenio/vibeguard) as the
   gate, but any diff-aware checker works the same way.

> **No runtime dependency.** contextweaver does not import or require the gate,
> and contextweaver is **not** a security scanner. The gate is a separate
> process you run after the agent step. Keep it that way — it is what makes the
> check deterministic and auditable.

### The loop

```python
import subprocess

from contextweaver.context.manager import ContextManager
from contextweaver.types import ContextItem, ItemKind, Phase

mgr = ContextManager()
mgr.ingest_sync(ContextItem(id="u1", kind=ItemKind.user_turn, text="fix the bug"))

# 1. Compile the answer-phase prompt and let the agent produce a diff.
pack = mgr.build_sync(phase=Phase.answer, query="fix the bug")
# diff = your_llm(pack.prompt)  -> apply the edit to the working tree

# 2. Run the gate as a plain subprocess, OUTSIDE the LLM context path.
result = subprocess.run(
    ["vibeguard", "gate", "--diff", "--fail-on", "high"],
    capture_output=True,
    text=True,
)
gate_passed = result.returncode == 0

# 3. Record the gate verdict back into the event log as a tool_result, so a
#    later interpret/answer turn can reason about *why* a change was blocked.
mgr.ingest_tool_result_sync(
    tool_call_id="gate-001",
    raw_output=result.stdout,
    tool_name="vibeguard_gate",
)
```

The gate output flows back through the context firewall like any other tool
result: a compact summary enters the prompt and the full report is stored
out-of-band under `mgr.artifact_store`. The model never sees the raw scanner
output unless it drills into the artifact.

### As a CI step

The same gate belongs in CI, where it is the merge-blocking check rather than an
in-loop signal:

```yaml
# .github/workflows/safety-gate.yml (sketch)
- name: Safety gate on the proposed diff
  run: vibeguard gate --diff origin/main...HEAD --fail-on high
```

This keeps the boundary clean: contextweaver shapes what the agent reads, the
agent writes, and a deterministic gate — local or in CI — has the final say on
what merges.

---

## See also

- [How contextweaver Fits](interop.md) — boundary, hook points, non-goals
- [MCP Integration](integration_mcp.md) ·
  [A2A Integration](integration_a2a.md)
- Framework guides:
  [LlamaIndex](integration_llamaindex.md) ·
  [LangChain + LangGraph](integration_langchain.md) ·
  [OpenAI ADK](integration_openai_adk.md) ·
  [Google ADK](integration_google_adk.md) ·
  [Pipecat](integration_pipecat.md) ·
  [CrewAI](integration_crewai.md) ·
  [External Memory](integration_memory.md)
- Existing examples directory: [`examples/`](https://github.com/dgenio/contextweaver/tree/main/examples)

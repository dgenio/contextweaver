# OpenAI Agents SDK Integration

> Wire contextweaver's bounded-choice routing and context firewall into
> the [OpenAI Agents SDK](https://platform.openai.com/docs/guides/agents)
> so single agents and Swarm-style hand-offs both share one budget-aware
> context.

## Importable adapter

As of issue #501, `contextweaver.adapters.openai_agents` ships first-class
converters for the SDK's function tools and run items:

```python
from contextweaver.adapters.openai_agents import (
    load_openai_agents_catalog,
    from_openai_agents_run,
)

catalog = load_openai_agents_catalog(agent.tools, namespace="ops")
# ... after a run, ingest its items (tool calls keep their outputs as children):
items = from_openai_agents_run(result.new_items, into=manager)
```

Install the optional extra for live loading (`pip install
'contextweaver[openai-agents]'`); the plain-dict / item-dict paths
(`openai_agents_tools_to_catalog`, `from_openai_agents_run`) need no extra and
are handy in tests.

## Why

The OpenAI Agents SDK (often called OpenAI ADK) is great at orchestrating
function-calling agents and Swarm-style hand-offs, but it leaves three
problems open:

- **Context explosion across hand-offs.** Each agent in a Swarm tends to
  ship its full history forward on a hand-off, multiplying tokens.
- **Every tool is in the prompt.** Function definitions in the
  `tools=[…]` list go into the system prompt unconditionally.
- **Large function results consume the context window.** A 10 KB JSON
  blob is fine for the tool, expensive for the model.

contextweaver fixes all three without forking the SDK or rewriting your
agent loop.

## Prerequisites

```bash
pip install contextweaver openai
export OPENAI_API_KEY=sk-...
```

The examples below use the Chat Completions API directly so they work
against both the Agents SDK and a plain `openai` client; if you're using
[`openai-agents-python`](https://github.com/openai/openai-agents-python)
the same patterns hold — replace `client.chat.completions.create(...)`
with `Agent.run(...)` and the integration points stay the same.

## Architecture

```text
User query
   │
   ▼
contextweaver Router          ← all functions registered in Catalog
   │ (top-k shortlist)
   ▼
Agent A (e.g. Product)        ← receives only the shortlist
   │ (function call OR hand-off)
   ▼
[hand-off] ──► Agent B (e.g. Billing)
   │
   ▼
contextweaver Firewall        ← intercepts large function results
   │ (summary + artifact handle)
   ▼
contextweaver ContextManager  ← unified context across all agents
   │ (pack.prompt for next call)
   ▼
LLM
```

The crucial detail: **one `ContextManager` for the whole Swarm**, not
one per agent. Hand-offs become a no-op for context because every agent
ingests into the same event log.

## Minimal Swarm with shared context

```python
import json

from openai import OpenAI

from contextweaver.context.manager import ContextManager
from contextweaver.routing.catalog import Catalog
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import ContextItem, ItemKind, Phase, SelectableItem


client = OpenAI()
ctx_mgr = ContextManager()   # one manager for the whole Swarm


def check_inventory(sku: str) -> str:
    """Return stock level for the given SKU."""
    return '{"sku": "...", "in_stock": 42}'


def get_invoice(invoice_id: str) -> str:
    """Return invoice JSON for the given ID."""
    return '{"invoice": "..."}'


FUNCTIONS = {
    "check_inventory": check_inventory,
    "get_invoice": get_invoice,
}


# 1. Register every function in contextweaver's Catalog.
catalog = Catalog()
for name, fn in FUNCTIONS.items():
    catalog.register(SelectableItem(
        id=name,
        kind="tool",
        name=name,
        description=(fn.__doc__ or "").strip().splitlines()[0],
        namespace="billing" if "invoice" in name else "product",
    ))
graph = TreeBuilder(max_children=8).build(catalog.all())
router = Router(graph, items=catalog.all(), top_k=3)


# 2. Decide which agent handles a routed shortlist.
def pick_agent(routed_ids: list[str]) -> str:
    if any(rid in {"get_invoice", "update_payment_method"} for rid in routed_ids):
        return "Billing"
    return "Product"


# 3. Per-turn loop with hand-off.
def respond(user_query: str, turn: int) -> str:
    ctx_mgr.ingest_sync(ContextItem(
        id=f"u{turn}", kind=ItemKind.user_turn, text=user_query,
    ))

    routed = router.route(user_query)
    agent_name = pick_agent(routed.candidate_ids)

    # Phase.call → arguments-assembly prompt with only the routed functions.
    pack_call = ctx_mgr.build_sync(phase=Phase.call, query=user_query)
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": f"You are the {agent_name} agent."},
            {"role": "user", "content": pack_call.prompt},
        ],
        functions=[
            {"name": rid, "description": catalog.get(rid).description}
            for rid in routed.candidate_ids
        ],
    )

    msg = response.choices[0].message
    if msg.function_call:
        # function_call.arguments is a JSON string; parse before invoking
        # the Python callable so structured arguments flow correctly.
        fn_args = json.loads(msg.function_call.arguments or "{}")
        result = FUNCTIONS[msg.function_call.name](**fn_args)
        ctx_mgr.ingest_sync(ContextItem(
            id=f"tc-{turn}", kind=ItemKind.tool_call,
            text=f"{msg.function_call.name}(...)", parent_id=f"u{turn}",
        ))
        ctx_mgr.ingest_tool_result_sync(
            tool_call_id=f"tc-{turn}",
            raw_output=str(result),
            tool_name=msg.function_call.name,
        )

    # Phase.answer → final response prompt; firewall summary is in there.
    pack_answer = ctx_mgr.build_sync(phase=Phase.answer, query=user_query)
    final = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": f"You are the {agent_name} agent."},
            {"role": "user", "content": pack_answer.prompt},
        ],
    )
    return str(final.choices[0].message.content)
```

Hand-offs work the same way: change the `system` message to the new
agent name; the next `build_sync()` still sees every prior turn because
the event log is shared.

## Wrapping every function with the firewall

If you call functions outside the `respond()` loop above — for example,
inside an Agents-SDK `function_tool` decorator — wrap each function so
results always go through the firewall:

```python
def _firewalled(fn, tool_call_id_factory):
    def wrapped(*args, **kwargs):
        raw = fn(*args, **kwargs)
        item, _ = ctx_mgr.ingest_tool_result_sync(
            tool_call_id=tool_call_id_factory(),
            raw_output=str(raw),
            tool_name=fn.__name__,
        )
        return item.text   # what the LLM sees
    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    return wrapped
```

Plug the wrapped version into whichever tool registry the SDK uses; the
agent sees a summary, while the artifact store keeps the raw bytes
addressable via the
[drilldown API](cookbook.md#4-firewall-drilldown-for-large-tool-outputs).

## Advanced patterns

- **Episodic memory across sessions** — persist
  `mgr.event_log.to_dict()` after the Swarm completes and re-hydrate at
  the start of the next session so the customer's prior context is
  available without re-reading it into the prompt.
- **Per-agent budgets** — pass `ContextBudget(...)` to `ContextManager`
  on construction; you can also build an `interpret`-phase prompt
  per-hand-off to keep the new agent oriented without blowing tokens.
- **Strict / seeded / adaptive modes** — `ProfileConfig` lets you fix a
  deterministic mode for production replays.

## Troubleshooting

- **Function definitions still bloat the prompt.** You're passing the
  full `functions=[...]` list to the model. Use
  `routed.candidate_ids` to filter, as in the example above.
- **Hand-off agent doesn't see prior turns.** Confirm both agents share
  the **same** `ContextManager` instance — a fresh one per agent loses
  the prior event log.
- **Function-calling loop runs forever.** contextweaver doesn't decide
  whether to call another function — the SDK does. Use
  `Router.route(..., exclude_ids=[...])` to prevent the router from
  re-recommending a tool the agent just used.
- **Token budget tuning.** Inspect `pack.stats` after each build; if
  `dropped_count` is high, increase the relevant phase budget. The
  defaults (`route=2000`, `call=3000`, `interpret=4000`, `answer=6000`)
  are conservative for gpt-4.

## See also

- [How contextweaver Fits](interop.md) — boundary diagram, hook points
- [Cookbook](cookbook.md) — copy-paste recipes
- [Google ADK Integration](integration_google_adk.md) — sister guide
- [OpenAI Agents SDK docs](https://platform.openai.com/docs/guides/agents)
- Tracking issue: [#78](https://github.com/dgenio/contextweaver/issues/78)

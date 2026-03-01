# A2A Integration

This guide covers how to integrate contextweaver with A2A (Agent-to-Agent)
patterns. contextweaver provides adapters for converting agent descriptors,
handling agent responses, and loading recorded multi-agent sessions.

## Overview

The A2A adapter (`contextweaver.adapters.a2a`) provides three main functions:

| Function                      | Input                      | Output              |
|-------------------------------|----------------------------|---------------------|
| `agent_to_item`               | Agent descriptor dict      | `SelectableItem`    |
| `agent_response_to_envelope`  | Agent response dict        | `ResultEnvelope`    |
| `load_a2a_session_jsonl`      | Path to JSONL session file | `list[ContextItem]` |

## The A2A Problem

In multi-agent systems, an orchestrator agent delegates tasks to specialist
agents. This creates context management challenges:

1. **Multiple sources** -- responses come from different agents with different
   formats and verbosity levels
2. **Nested delegation** -- agent A delegates to agent B, which calls a tool,
   which returns to B, which responds to A
3. **Agent selection** -- the orchestrator must choose which agent to delegate
   to, similar to tool selection

contextweaver addresses these by treating agents as `SelectableItem` objects
(kind="agent") and agent responses as tool results that flow through the
same context firewall pipeline.

## Converting Agent Descriptors

A2A agent descriptors (similar to agent cards in the A2A protocol) are
converted to `SelectableItem` objects for routing:

```python
from contextweaver.adapters.a2a import agent_to_item

agent_info = {
    "name": "DataAgent",
    "description": "Retrieves and processes structured data from databases.",
    "skills": [
        {"id": "db_query", "name": "Database Query"},
        {"id": "data_transform", "name": "Data Transform"},
    ],
}

item = agent_to_item(agent_info)
# item.id         -> "a2a.DataAgent"
# item.kind       -> "agent"
# item.namespace  -> "a2a"
# item.tags       -> ["Database Query", "Data Transform"]
```

### Field Mapping

| Agent descriptor field | SelectableItem field |
|------------------------|----------------------|
| `name`                 | `id` (prefixed "a2a."), `name` |
| `description`          | `description`        |
| `skills[].name`        | `tags`               |

The `kind` is always `"agent"` (distinguishing agents from tools in the
routing engine). The `namespace` is always `"a2a"`.

## Handling Agent Responses

When a delegate agent returns a response, convert it to a `ResultEnvelope`:

```python
from contextweaver.adapters.a2a import agent_response_to_envelope

response = {
    "status": "ok",
    "text": "Q3 revenue was $2.4M, up 15% from Q2.",
}

envelope = agent_response_to_envelope(response)
# envelope.status  -> "ok"
# envelope.summary -> "Q3 revenue was $2.4M, up 15% from Q2."
# envelope.facts   -> {"source": "a2a"}
```

For large agent responses, you can provide an `artifact_store` and a custom
`summarizer`:

```python
from contextweaver.store.artifacts import InMemoryArtifactStore

store = InMemoryArtifactStore()
envelope = agent_response_to_envelope(
    response,
    artifact_store=store,
    summarizer=my_summarizer,
)
```

## Loading Recorded Sessions

Load a multi-agent session from JSONL for replay or analysis:

```python
from contextweaver.adapters.a2a import load_a2a_session_jsonl

items = load_a2a_session_jsonl("a2a_session.jsonl")
```

### JSONL Format

Each line is a JSON object. The `source` field tracks which agent produced
the event:

```json
{"type": "user_turn", "id": "u1", "text": "Summarize Q3 sales", "timestamp": 1700000000.0, "source": "user"}
{"type": "agent_msg", "id": "a1", "text": "Delegating to DataAgent...", "timestamp": 1700000001.0, "source": "orchestrator"}
{"type": "tool_call", "id": "tc1", "text": "Delegating: retrieve sales data", "timestamp": 1700000002.0, "source": "orchestrator"}
{"type": "tool_result", "id": "tr1", "tool_call_id": "tc1", "text": "Revenue: $2.4M...", "timestamp": 1700000003.0, "source": "DataAgent"}
```

The `source` field is stored in `ContextItem.metadata["source"]`, making it
available for scoring and filtering.

## Multi-Agent Routing

contextweaver's routing engine works seamlessly with mixed catalogs of tools
and agents:

```python
from contextweaver.adapters.a2a import agent_to_item
from contextweaver.adapters.mcp import mcp_tool_to_item
from contextweaver.routing.tree import TreeBuilder
from contextweaver.routing.router import Router

# Build a unified catalog
items = []
items.extend(mcp_tool_to_item(t) for t in mcp_tools)    # kind="tool"
items.extend(agent_to_item(a) for a in agent_cards)       # kind="agent"

# Route over mixed catalog
graph = TreeBuilder().build(items)
router = Router(graph, top_k=10)
result = router.route("analyze customer churn data")
# result.candidate_items may include both tools and agents
```

The `kind` field on each `ChoiceCard` tells the LLM whether a candidate is
a tool (call directly) or an agent (delegate to).

## Integration Pattern

A typical multi-agent orchestration with contextweaver:

```python
from contextweaver.adapters.a2a import agent_to_item, agent_response_to_envelope
from contextweaver.context.manager import ContextManager
from contextweaver.types import ContextItem, ItemKind, Phase

# Setup
mgr = ContextManager()
agents = [agent_to_item(a) for a in discover_agents()]

# 1. User sends a request
mgr.ingest_sync(ContextItem(
    id="u1", kind=ItemKind.USER_TURN,
    text=user_request, token_estimate=len(user_request) // 4,
))

# 2. Orchestrator decides which agent to delegate to (ROUTE phase)
route_pack = mgr.build_sync(
    goal="Choose the best agent for this task",
    phase=Phase.ROUTE,
)

# 3. Delegate to chosen agent, ingest the response
agent_response = call_agent(chosen_agent, task_description)
mgr.ingest_sync(ContextItem(
    id="tc1", kind=ItemKind.TOOL_CALL,
    text=f"Delegate to {chosen_agent.name}: {task_description}",
    token_estimate=20,
))

# 4. Ingest agent response (through firewall if large)
item, envelope = mgr.ingest_tool_result_sync(
    tool_call_id="tc1",
    raw_output=agent_response["text"],
    tool_name=chosen_agent.name,
)

# 5. Store semantic facts from the response
for key, value in envelope.facts.items():
    mgr.add_fact_sync(key, str(value))

# 6. Build context for the answer
answer_pack = mgr.build_sync(
    goal="Answer the user based on agent results",
    phase=Phase.ANSWER,
)
```

## Tracking Agent Provenance

In multi-agent sessions, tracking which agent produced which information is
important. contextweaver supports this through metadata:

```python
# When ingesting, include source in metadata
mgr.ingest_sync(ContextItem(
    id="tr1",
    kind=ItemKind.TOOL_RESULT,
    text=agent_response_text,
    token_estimate=len(agent_response_text) // 4,
    metadata={"source": "DataAgent", "timestamp": time.time()},
    parent_id="tc1",
))
```

The `source` metadata is preserved through the pipeline and appears in the
rendered context, allowing the final LLM to attribute information to
specific agents.

## Episodic Summaries for Long Sessions

Multi-agent sessions can be long. Use episodic summaries to compress earlier
segments:

```python
# After each agent delegation round, store a summary
mgr.add_episode_sync(
    "round_1",
    "DataAgent retrieved Q3 sales data showing $2.4M revenue, 15% growth."
)
mgr.add_episode_sync(
    "round_2",
    "CommsAgent drafted a team email highlighting APAC underperformance."
)
```

The three most recent episodic summaries are automatically included in every
context build, providing continuity even when earlier items are dropped from
the budget.

## Example

See `examples/a2a_adapter_demo.py` for a complete, runnable demonstration
that loads a recorded A2A session, ingests it, and builds context for all
four phases.

# A2A Integration

contextweaver provides an adapter for the
[Agent-to-Agent (A2A) protocol](https://google.github.io/A2A/) that
converts A2A agent cards and task results into contextweaver's native
types.

## Adapter functions

### `a2a_agent_to_selectable(agent_card)`

Converts an A2A agent card dict into a `SelectableItem`:

```python
from contextweaver.adapters.a2a import a2a_agent_to_selectable

agent_card = {
    "name": "DataAgent",
    "description": "Retrieves and aggregates data from warehouses",
    "url": "https://agents.example.com/data",
    "skills": [
        {"id": "sql_query", "name": "SQL Query", "description": "Run SQL queries"},
        {"id": "aggregate", "name": "Aggregate", "description": "Aggregate results"},
    ]
}

item = a2a_agent_to_selectable(agent_card)
# item.id    == "DataAgent"
# item.kind  == "agent"
# item.name  == "DataAgent"
# item.tags  includes skill names
```

### `a2a_result_to_envelope(task_result, agent_name)`

Converts an A2A task result dict into a `ResultEnvelope`:

```python
from contextweaver.adapters.a2a import a2a_result_to_envelope

task_result = {
    "status": {"state": "completed"},
    "artifacts": [
        {"parts": [{"type": "text", "text": "Q4 revenue: $2.1M, +15% YoY"}]}
    ]
}

envelope = a2a_result_to_envelope(task_result, "DataAgent")
# envelope.summary contains the artifact text
# envelope.status  == "ok"
```

### `load_a2a_session_jsonl(path)`

Loads a JSONL session file containing A2A-style multi-agent events:

```python
from contextweaver.adapters.a2a import load_a2a_session_jsonl

items = load_a2a_session_jsonl("examples/data/a2a_session.jsonl")
```

## Session JSONL format

Each line is a JSON object. A2A sessions typically involve multi-agent
handoffs where an orchestrator delegates to specialised agents:

```json
{"id": "u1", "type": "user_turn", "text": "Generate the Q4 report"}
{"id": "tc1", "type": "tool_call", "text": "delegate_to(DataAgent, 'fetch Q4 data')", "parent_id": "u1"}
{"id": "tr1", "type": "tool_result", "content": "...", "parent_id": "tc1"}
```

See `examples/data/a2a_session.jsonl` for a complete multi-agent
session.

## End-to-end example

```python
from contextweaver.adapters.a2a import (
    a2a_agent_to_selectable,
    a2a_result_to_envelope,
    load_a2a_session_jsonl,
)
from contextweaver.context.manager import ContextManager
from contextweaver.types import ItemKind, Phase

# Load multi-agent session
items = load_a2a_session_jsonl("examples/data/a2a_session.jsonl")

# Build context
mgr = ContextManager()
for item in items:
    if item.kind == ItemKind.tool_result and len(item.text) > 2000:
        mgr.ingest_tool_result(
            tool_call_id=item.parent_id or item.id,
            raw_output=item.text,
            tool_name="a2a_agent",
        )
    else:
        mgr.ingest(item)

pack = mgr.build_sync(phase=Phase.answer, query="Q4 report")
print(pack.prompt)
```

See `examples/a2a_adapter_demo.py` for the full runnable demo.

# MCP Integration

contextweaver provides an adapter for the
[Model Context Protocol (MCP)](https://modelcontextprotocol.io/) that
converts MCP tool definitions and results into contextweaver's native
types.

## Adapter functions

### `mcp_tool_to_selectable(tool_dict)`

Converts an MCP tool definition dict into a `SelectableItem`:

```python
from contextweaver.adapters.mcp import mcp_tool_to_selectable

mcp_tool = {
    "name": "search_database",
    "description": "Search records in the database",
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 10}
        }
    }
}

item = mcp_tool_to_selectable(mcp_tool)
# item.id    == "mcp:search_database"
# item.kind  == "tool"
# item.name  == "search_database"
```

### `mcp_result_to_envelope(result_dict, tool_name)`

Converts an MCP tool result dict into a `ResultEnvelope`:

```python
from contextweaver.adapters.mcp import mcp_result_to_envelope

mcp_result = {
    "content": [{"type": "text", "text": "Found 42 records matching query"}],
    "isError": False
}

envelope = mcp_result_to_envelope(mcp_result, "search_database")
# envelope.summary contains the text content
# envelope.status  == "ok"
```

### `load_mcp_session_jsonl(path)`

Loads a JSONL session file containing MCP-style events and returns a
list of `ContextItem` objects:

```python
from contextweaver.adapters.mcp import load_mcp_session_jsonl

items = load_mcp_session_jsonl("examples/data/mcp_session.jsonl")
for item in items:
    print(f"{item.kind.value}: {item.text[:60]}...")
```

## Session JSONL format

Each line is a JSON object with at minimum `id`, `type`, and either
`text` or `content`:

```json
{"id": "u1", "type": "user_turn", "text": "Search for open invoices"}
{"id": "tc1", "type": "tool_call", "text": "invoices.search(status='open')", "parent_id": "u1"}
{"id": "tr1", "type": "tool_result", "content": "...", "parent_id": "tc1"}
```

See `examples/data/mcp_session.jsonl` for a complete example.

## End-to-end example

```python
from contextweaver.adapters.mcp import (
    load_mcp_session_jsonl,
    mcp_tool_to_selectable,
)
from contextweaver.context.manager import ContextManager
from contextweaver.types import ItemKind, Phase

# Load session events
items = load_mcp_session_jsonl("examples/data/mcp_session.jsonl")

# Build context with firewall
mgr = ContextManager()
for item in items:
    if item.kind == ItemKind.tool_result and len(item.text) > 2000:
        mgr.ingest_tool_result(
            tool_call_id=item.parent_id or item.id,
            raw_output=item.text,
            tool_name="mcp_tool",
        )
    else:
        mgr.ingest(item)

pack = mgr.build_sync(phase=Phase.answer, query="invoice status")
print(pack.prompt)
```

See `examples/mcp_adapter_demo.py` for the full runnable demo.

# MCP Integration

This guide covers how to integrate contextweaver with MCP (Model Context
Protocol) tool ecosystems. contextweaver provides adapters for converting
MCP tool definitions, handling tool results, and loading recorded sessions.

## Overview

The MCP adapter (`contextweaver.adapters.mcp`) provides three main functions:

| Function                  | Input                      | Output                |
|---------------------------|----------------------------|-----------------------|
| `mcp_tool_to_item`        | MCP tool schema dict       | `SelectableItem`      |
| `mcp_result_to_envelope`  | MCP tool result dict       | `ResultEnvelope`      |
| `load_mcp_session_jsonl`  | Path to JSONL session file | `list[ContextItem]`   |

## Converting Tool Definitions

MCP servers expose tool definitions with a standard schema. contextweaver
converts these to `SelectableItem` objects for use in the routing engine.

```python
from contextweaver.adapters.mcp import mcp_tool_to_item

mcp_tool = {
    "name": "search_invoices",
    "description": "Search invoices by date range and status",
    "inputSchema": {
        "type": "object",
        "properties": {
            "start_date": {"type": "string"},
            "end_date": {"type": "string"},
            "status": {"type": "string", "enum": ["paid", "pending", "overdue"]},
        },
        "required": ["start_date"],
    },
    "annotations": {
        "tags": ["billing", "search"],
        "sideEffects": False,
        "costHint": "low",
    },
}

item = mcp_tool_to_item(mcp_tool)
# item.id         -> "mcp.search_invoices"
# item.kind       -> "tool"
# item.namespace  -> "mcp"
# item.tags       -> ["billing", "search"]
# item.args_schema -> the inputSchema dict
# item.side_effects -> False
# item.cost_hint   -> "low"
```

### Field Mapping

| MCP field                    | SelectableItem field |
|------------------------------|----------------------|
| `name`                       | `id` (prefixed "mcp."), `name` |
| `description`                | `description`        |
| `inputSchema`                | `args_schema`        |
| `annotations.tags`           | `tags`               |
| `annotations.sideEffects`    | `side_effects`       |
| `annotations.costHint`       | `cost_hint`          |

The `namespace` is always set to `"mcp"`. The `kind` is always `"tool"`.

## Handling Tool Results

When an MCP tool returns a result, use `mcp_result_to_envelope` to convert
it into a `ResultEnvelope` with summary, facts, and optional artifact storage.

```python
from contextweaver.adapters.mcp import mcp_result_to_envelope
from contextweaver.store.artifacts import InMemoryArtifactStore

mcp_result = {
    "content": [
        {"type": "text", "text": "Found 42 records matching your query."},
        {"type": "text", "text": "Top result: Invoice #1001 - $4,500"},
    ],
    "isError": False,
}

# Without artifact storage (just summary + facts)
envelope = mcp_result_to_envelope(mcp_result)

# With artifact storage (stores large results out-of-band)
store = InMemoryArtifactStore()
envelope = mcp_result_to_envelope(mcp_result, artifact_store=store)
```

The function:
1. Concatenates all `text`-type content parts
2. Detects `isError` and sets `status` accordingly
3. Runs `RuleBasedSummarizer` to produce a concise summary
4. Runs `StructuredExtractor` to extract facts
5. If the content exceeds 2000 chars and an `artifact_store` is provided,
   stores the full text out-of-band

### Custom Summarizer

You can pass a custom `Summarizer` implementation:

```python
envelope = mcp_result_to_envelope(
    mcp_result,
    summarizer=my_custom_summarizer,
    extractor=my_custom_extractor,
)
```

## Ingesting via ContextManager

For the most common workflow, use `ContextManager.ingest_tool_result_sync`
which handles firewall, storage, and event log ingestion in one call:

```python
from contextweaver.context.manager import ContextManager

mgr = ContextManager()

# Ingest a user turn
mgr.ingest_sync(ContextItem(
    id="u1",
    kind=ItemKind.USER_TURN,
    text="Find unpaid invoices",
    token_estimate=5,
))

# Ingest a tool call
mgr.ingest_sync(ContextItem(
    id="tc1",
    kind=ItemKind.TOOL_CALL,
    text='search_invoices(status="unpaid")',
    token_estimate=6,
    parent_id="u1",
))

# Ingest the tool result through the firewall
item, envelope = mgr.ingest_tool_result_sync(
    tool_call_id="tc1",
    raw_output=mcp_result_text,
    tool_name="search_invoices",
    media_type="text/plain",
    firewall_threshold=2000,
)
# item is now in the event log with a summary
# envelope contains facts and artifact references
```

## Loading Recorded Sessions

For replay, testing, or batch processing, contextweaver can load a full
MCP session from a JSONL file:

```python
from contextweaver.adapters.mcp import load_mcp_session_jsonl

items = load_mcp_session_jsonl("session.jsonl")
# items is a list[ContextItem] with parent_id links
```

### JSONL Format

Each line is a JSON object with a `type` field:

```json
{"type": "user_turn", "id": "u1", "text": "Find invoices", "timestamp": 1700000000.0}
{"type": "tool_call", "id": "tc1", "tool_name": "search", "args": {"q": "invoices"}, "timestamp": 1700000001.0}
{"type": "tool_result", "id": "tr1", "tool_call_id": "tc1", "content": "Found 5 invoices...", "timestamp": 1700000002.0}
{"type": "agent_msg", "id": "a1", "text": "Here are the results.", "timestamp": 1700000003.0}
```

Supported event types:

| `type`        | Maps to ItemKind  | Key fields                        |
|---------------|-------------------|-----------------------------------|
| `user_turn`   | `USER_TURN`       | `text`                            |
| `tool_call`   | `TOOL_CALL`       | `tool_name`, `args`               |
| `tool_result` | `TOOL_RESULT`     | `content`, `tool_call_id` (parent)|
| `agent_msg`   | `AGENT_MSG`       | `text`                            |

The `tool_call_id` field on `tool_result` events is converted to `parent_id`,
establishing the tool_call -> tool_result dependency chain used by the
selection stage's dependency closure.

## Integration Pattern

A typical MCP integration looks like:

```python
from contextweaver.adapters.mcp import mcp_tool_to_item, load_mcp_session_jsonl
from contextweaver.context.manager import ContextManager
from contextweaver.routing.tree import TreeBuilder
from contextweaver.routing.router import Router
from contextweaver.types import Phase

# 1. Convert MCP tool definitions to SelectableItems
items = [mcp_tool_to_item(tool) for tool in mcp_server.list_tools()]

# 2. Build routing graph
graph = TreeBuilder(max_children=15).build(items)
router = Router(graph, beam_width=3, top_k=10)

# 3. Create context manager
mgr = ContextManager()

# 4. In the agent loop:
#    a. Route to find relevant tools
result = router.route(user_query)

#    b. Build ROUTE-phase context with choice cards
pack, cards, prompt = mgr.build_route_prompt_sync(
    goal=user_query,
    query=user_query,
    router=router,
)

#    c. LLM picks a tool, agent calls it, ingest result
item, envelope = mgr.ingest_tool_result_sync(
    tool_call_id="tc1",
    raw_output=mcp_call_result,
    tool_name=chosen_tool.name,
)

#    d. Build INTERPRET and ANSWER phase contexts
interpret_pack = mgr.build_sync(goal, Phase.INTERPRET)
answer_pack = mgr.build_sync(goal, Phase.ANSWER)
```

## Example

See `examples/mcp_adapter_demo.py` for a complete, runnable demonstration
that loads a recorded MCP session and builds context for multiple phases.

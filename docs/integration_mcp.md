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
    },
    "outputSchema": {
        "type": "object",
        "properties": {
            "results": {"type": "array"},
            "total": {"type": "integer"}
        }
    }
}

item = mcp_tool_to_selectable(mcp_tool)
# item.id            == "mcp:search_database"
# item.kind          == "tool"
# item.name          == "search_database"
# item.output_schema == {"type": "object", ...}
```

If the tool definition includes an `outputSchema`, it is preserved in
`item.output_schema`.  When absent the field is `None`.

The namespace is inferred automatically from the tool name prefix:

| Tool name              | Inferred namespace |
|------------------------|--------------------|
| `github.create_issue`  | `github`           |
| `filesystem/read`      | `filesystem`       |
| `slack_send_message`   | `slack`            |
| `search_database`      | `mcp` (fallback)   |

Use `infer_namespace(tool_name)` directly if you need the logic outside of
`mcp_tool_to_selectable()`.

### `mcp_result_to_envelope(result_dict, tool_name)`

Converts an MCP tool result dict into a `ResultEnvelope`:

```python
from contextweaver.adapters.mcp import mcp_result_to_envelope

mcp_result = {
    "content": [{"type": "text", "text": "Found 42 records matching query"}],
    "isError": False
}

envelope, binaries, full_text = mcp_result_to_envelope(mcp_result, "search_database")
# envelope.summary contains truncated text (max 500 chars)
# full_text contains the complete untruncated text
# envelope.status  == "ok"
# binaries maps handle → (raw_bytes, media_type, label)
```

#### Supported content types

| Content type    | Handling                                                                                      |
|-----------------|-----------------------------------------------------------------------------------------------|
| `text`          | Concatenated into `full_text` and `summary`                                                   |
| `image`         | Base64-decoded; stored as binary artifact                                                     |
| `audio`         | Base64-decoded; stored as binary artifact (e.g. `audio/wav`)                                  |
| `resource`      | Text extracted into `full_text`; raw bytes stored as artifact                                 |
| `resource_link` | URI stored as `ArtifactRef` (`size_bytes=0`); URI string in `binaries` for caller resolution  |

#### Structured content

If the result contains a top-level `structuredContent` dict, it is
serialized as a JSON artifact and its top-level keys are extracted as
facts:

```python
mcp_result = {
    "content": [{"type": "text", "text": "query done"}],
    "structuredContent": {"count": 42, "status": "done"},
}
envelope, binaries, _ = mcp_result_to_envelope(mcp_result, "query")
# binaries["mcp:query:structured_content"] → JSON bytes
# envelope.facts includes "count: 42", "status: done"
```

#### Content-part annotations

Per-part `annotations` (with `audience` and `priority` fields) are
collected into `envelope.provenance["content_annotations"]`:

```python
mcp_result = {
    "content": [
        {"type": "text", "text": "...", "annotations": {"audience": ["human"], "priority": 0.9}},
    ],
}
envelope, _, _ = mcp_result_to_envelope(mcp_result, "tool")
# envelope.provenance["content_annotations"] == [{"part_index": 0, "audience": ["human"], ...}]
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

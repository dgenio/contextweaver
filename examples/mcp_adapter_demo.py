"""MCP adapter demo (placeholder).

This example will demonstrate converting MCP tool definitions and results
into contextweaver-native types once the MCP adapter is fully implemented.
"""

from __future__ import annotations

MCP_TOOL_DEF = {
    "name": "search_db",
    "description": "Search records in the database",
    "inputSchema": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
}

MCP_TOOL_RESULT = {
    "content": [{"type": "text", "text": "Found 42 records matching your query."}],
    "isError": False,
}


def main() -> None:
    print("MCP adapter demo — implementation pending.")
    print(f"Example tool def: {MCP_TOOL_DEF['name']!r}")
    print(f"Example result content: {MCP_TOOL_RESULT['content'][0]['text']!r}")


if __name__ == "__main__":
    main()

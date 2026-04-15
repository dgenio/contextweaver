"""Adapters sub-package for contextweaver.

Provides thin adapters that convert external protocol data (MCP, A2A, FastMCP)
into contextweaver-native types.
"""

from __future__ import annotations

from contextweaver.adapters.a2a import (
    a2a_agent_to_selectable,
    a2a_result_to_envelope,
    load_a2a_session_jsonl,
)
from contextweaver.adapters.fastmcp import (
    fastmcp_tool_to_selectable,
    fastmcp_tools_to_catalog,
    infer_fastmcp_namespace,
    load_fastmcp_catalog,
)
from contextweaver.adapters.mcp import (
    infer_namespace,
    load_mcp_session_jsonl,
    mcp_result_to_envelope,
    mcp_tool_to_selectable,
)

__all__ = [
    "a2a_agent_to_selectable",
    "a2a_result_to_envelope",
    "fastmcp_tool_to_selectable",
    "fastmcp_tools_to_catalog",
    "infer_fastmcp_namespace",
    "infer_namespace",
    "load_a2a_session_jsonl",
    "load_fastmcp_catalog",
    "load_mcp_session_jsonl",
    "mcp_result_to_envelope",
    "mcp_tool_to_selectable",
]

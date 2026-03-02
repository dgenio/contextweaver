"""Adapters sub-package for contextweaver.

Provides thin adapters that convert external protocol data (MCP, A2A) into
contextweaver-native types.
"""

from __future__ import annotations

from contextweaver.adapters.a2a import (
    a2a_agent_to_selectable,
    a2a_result_to_envelope,
    load_a2a_session_jsonl,
)
from contextweaver.adapters.mcp import (
    load_mcp_session_jsonl,
    mcp_result_to_envelope,
    mcp_tool_to_selectable,
)

__all__ = [
    "a2a_agent_to_selectable",
    "a2a_result_to_envelope",
    "load_a2a_session_jsonl",
    "load_mcp_session_jsonl",
    "mcp_result_to_envelope",
    "mcp_tool_to_selectable",
]

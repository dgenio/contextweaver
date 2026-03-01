"""Adapters sub-package for contextweaver.

Provides thin adapters that convert external protocol data (MCP, A2A) into
contextweaver-native types.
"""

from contextweaver.adapters.a2a import a2a_agent_to_selectable, a2a_result_to_envelope
from contextweaver.adapters.mcp import mcp_result_to_envelope, mcp_tool_to_selectable

__all__ = [
    "a2a_agent_to_selectable",
    "a2a_result_to_envelope",
    "mcp_result_to_envelope",
    "mcp_tool_to_selectable",
]

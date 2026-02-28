"""Tests for contextweaver adapters (MCP and A2A).

These adapters are not yet implemented; tests verify the correct exception
is raised.
"""

from __future__ import annotations

import pytest

from contextweaver.adapters.a2a import a2a_agent_to_selectable, a2a_result_to_envelope
from contextweaver.adapters.mcp import mcp_result_to_envelope, mcp_tool_to_selectable


def test_mcp_tool_to_selectable_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        mcp_tool_to_selectable({"name": "tool", "description": "does stuff"})


def test_mcp_result_to_envelope_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        mcp_result_to_envelope({"content": []}, "tool_name")


def test_a2a_agent_to_selectable_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        a2a_agent_to_selectable({"name": "agent", "description": "agent desc"})


def test_a2a_result_to_envelope_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        a2a_result_to_envelope({"status": "completed"}, "agent_name")

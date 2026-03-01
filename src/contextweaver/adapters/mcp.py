"""MCP (Model Context Protocol) adapter for contextweaver.

Provides helpers for converting MCP tool definitions into
:class:`~contextweaver.types.SelectableItem` objects and wrapping MCP tool
call results as :class:`~contextweaver.types.ResultEnvelope` instances.
"""

from __future__ import annotations

from typing import Any

from contextweaver.envelope import ResultEnvelope
from contextweaver.types import SelectableItem


def mcp_tool_to_selectable(tool_def: dict[str, Any]) -> SelectableItem:
    """Convert an MCP tool definition dict to a :class:`~contextweaver.types.SelectableItem`.

    Expected keys in *tool_def*: ``name``, ``description``, ``inputSchema``
    (optional), ``annotations`` (optional).

    Args:
        tool_def: Raw MCP tool definition as returned by ``tools/list``.

    Returns:
        A :class:`~contextweaver.types.SelectableItem` with ``kind="tool"``
        and ``namespace="mcp"``.

    Raises:
        NotImplementedError: Pending implementation — see plan v8.
    """
    raise NotImplementedError("Pending implementation — see plan v8")


def mcp_result_to_envelope(
    result: dict[str, Any],
    tool_name: str,
) -> ResultEnvelope:
    """Convert an MCP tool call result to a :class:`~contextweaver.types.ResultEnvelope`.

    Args:
        result: Raw MCP tool result dict (``content`` list + optional ``isError``).
        tool_name: The name of the tool that produced the result.

    Returns:
        A :class:`~contextweaver.types.ResultEnvelope`.

    Raises:
        NotImplementedError: Pending implementation — see plan v8.
    """
    raise NotImplementedError("Pending implementation — see plan v8")

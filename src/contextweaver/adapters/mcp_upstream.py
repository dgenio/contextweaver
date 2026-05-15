"""Concrete :class:`UpstreamCall` adapters for the MCP proxy / gateway.

The :class:`ProxyRuntime` consumes a transport-agnostic
:class:`~contextweaver.adapters.proxy_runtime.UpstreamCall` Protocol so it
can be exercised in unit tests without spinning up a real MCP server.
This module ships two concrete implementations of that Protocol:

- :class:`StubUpstream` — in-process dict-shaped stub for tests and
  examples; constructs MCP-protocol results directly without going
  through the network.
- :class:`McpClientUpstream` — wraps an
  :class:`mcp.client.session.ClientSession` so a single upstream server
  is fronted by the runtime.  Multi-server fan-out is the caller's
  responsibility (compose multiple ``McpClientUpstream`` instances
  behind a :class:`MultiplexUpstream`).
- :class:`MultiplexUpstream` — fans out :meth:`list_tools` over several
  upstream sources and routes :meth:`call_tool` to the source that
  exported the named tool.

Each implementation returns MCP-format dicts (not the SDK's pydantic
types) because the runtime's downstream consumers
(:func:`~contextweaver.adapters.mcp.mcp_tool_to_selectable`,
:func:`~contextweaver.adapters.mcp.mcp_result_to_envelope`) speak dict.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("contextweaver.adapters.mcp_upstream")


class StubUpstream:
    """An in-process upstream wired up entirely from Python dicts.

    Useful for unit tests, examples, and air-gapped CI.  The caller
    supplies a static list of tool definitions and an optional handler
    callback that maps ``(tool_name, args) → MCP result dict``.

    Args:
        tool_defs: MCP-format tool definitions (``name``,
            ``description``, ``inputSchema``, ...).
        handler: Optional async callable invoked by :meth:`call_tool`.
            When omitted the default handler returns an ``isError=True``
            result with a "no handler configured" message.
    """

    def __init__(
        self,
        tool_defs: list[dict[str, Any]],
        handler: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self._tool_defs = [dict(t) for t in tool_defs]
        self._handler = handler

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return the configured tool definitions."""
        return [dict(t) for t in self._tool_defs]

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Dispatch to the configured handler or return a stub error."""
        if self._handler is not None:
            return await self._handler(tool_name, arguments)
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"stub upstream has no handler for {tool_name!r}",
                }
            ],
            "isError": True,
        }


class McpClientUpstream:
    """Adapt a single :class:`mcp.client.session.ClientSession` to :class:`UpstreamCall`.

    The wrapped session must already be connected; lifecycle management
    (connect / close, transport, auth) is the caller's responsibility.
    The MCP SDK's pydantic types are converted to plain dicts on the way
    out so downstream consumers continue to operate on the MCP wire
    format.

    Args:
        session: A connected :class:`mcp.client.session.ClientSession`.
    """

    def __init__(self, session: Any) -> None:  # noqa: ANN401 — MCP SDK ClientSession
        self._session = session

    async def list_tools(self) -> list[dict[str, Any]]:
        """Call ``tools/list`` upstream and return dict-shaped defs."""
        listing = await self._session.list_tools()
        tools = getattr(listing, "tools", listing) or []
        return [_tool_to_dict(t) for t in tools]

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Forward a tool call and return a dict-shaped MCP result."""
        try:
            result = await self._session.call_tool(tool_name, arguments)
        except Exception as exc:  # noqa: BLE001
            logger.debug("upstream call_tool %s failed: %s", tool_name, exc)
            return {
                "content": [{"type": "text", "text": f"upstream error: {exc}"}],
                "isError": True,
            }
        return _call_tool_result_to_dict(result)


class MultiplexUpstream:
    """Fan :meth:`list_tools` across multiple :class:`UpstreamCall` sources.

    :meth:`call_tool` routes the request to the source that exported the
    named tool.  When two upstreams expose tools with the same name, the
    first source registered wins; the runtime's canonical ``tool_id``
    cutover (§1.7) keeps this collision rare in practice.
    """

    def __init__(self, sources: list[Any]) -> None:  # noqa: ANN401 — UpstreamCall Protocol
        self._sources = list(sources)
        self._owner_index: dict[str, int] = {}

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return the union of tool defs across all sources."""
        out: list[dict[str, Any]] = []
        for idx, source in enumerate(self._sources):
            tools = await source.list_tools()
            for tool_def in tools:
                name = str(tool_def.get("name", ""))
                if name and name not in self._owner_index:
                    self._owner_index[name] = idx
                    out.append(tool_def)
        return out

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Forward the call to the source that owns *tool_name*."""
        idx = self._owner_index.get(tool_name)
        if idx is None:
            return {
                "content": [{"type": "text", "text": f"no upstream owns tool {tool_name!r}"}],
                "isError": True,
            }
        result: dict[str, Any] = await self._sources[idx].call_tool(tool_name, arguments)
        return result


def _tool_to_dict(tool: Any) -> dict[str, Any]:  # noqa: ANN401
    """Coerce a possibly-pydantic ``Tool`` object to a plain MCP dict."""
    if isinstance(tool, dict):
        return dict(tool)
    if hasattr(tool, "model_dump"):
        return dict(tool.model_dump())
    return {
        "name": getattr(tool, "name", ""),
        "description": getattr(tool, "description", ""),
        "inputSchema": getattr(tool, "inputSchema", {}) or {},
    }


def _call_tool_result_to_dict(result: Any) -> dict[str, Any]:  # noqa: ANN401
    """Coerce an MCP ``CallToolResult`` to the wire-format dict."""
    if isinstance(result, dict):
        return dict(result)
    if hasattr(result, "model_dump"):
        return dict(result.model_dump())
    return {
        "content": getattr(result, "content", []) or [],
        "isError": bool(getattr(result, "isError", False)),
    }

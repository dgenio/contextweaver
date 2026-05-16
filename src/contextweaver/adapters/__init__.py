"""Adapters sub-package for contextweaver.

Provides thin adapters that convert external protocol data (MCP, A2A, FastMCP,
weaver-spec) into contextweaver-native types and back, and shipping runtime
modes for fronting upstream MCP servers (proxy + gateway).
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
    make_context_hook,
    make_discovery_tool,
)
from contextweaver.adapters.gateway_error import GatewayError
from contextweaver.adapters.mcp import (
    infer_namespace,
    load_mcp_session_jsonl,
    mcp_result_to_envelope,
    mcp_tool_to_selectable,
)
from contextweaver.adapters.mcp_gateway import (
    GATEWAY_TOOL_NAMES,
    dispatch_meta_tool,
    make_gateway_meta_tools,
)
from contextweaver.adapters.mcp_proxy import (
    PROXY_META_TOOL_NAMES,
    dispatch_proxy_request,
    make_proxy_meta_tools,
    make_stripped_tools_list,
)
from contextweaver.adapters.mcp_upstream import (
    McpClientUpstream,
    MultiplexUpstream,
    StubUpstream,
)
from contextweaver.adapters.proxy_runtime import ExposureMode, ProxyRuntime, UpstreamCall
from contextweaver.adapters.weaver_contracts import (
    from_weaver_choice_card,
    from_weaver_choice_card_single,
    from_weaver_frame,
    from_weaver_routing_decision,
    from_weaver_selectable_item,
    to_weaver_choice_card,
    to_weaver_choice_cards,
    to_weaver_frame,
    to_weaver_routing_decision,
    to_weaver_selectable_item,
)

__all__ = [
    "ExposureMode",
    "GATEWAY_TOOL_NAMES",
    "GatewayError",
    "McpClientUpstream",
    "MultiplexUpstream",
    "PROXY_META_TOOL_NAMES",
    "ProxyRuntime",
    "StubUpstream",
    "UpstreamCall",
    "a2a_agent_to_selectable",
    "a2a_result_to_envelope",
    "dispatch_meta_tool",
    "dispatch_proxy_request",
    "fastmcp_tool_to_selectable",
    "fastmcp_tools_to_catalog",
    "from_weaver_choice_card",
    "from_weaver_choice_card_single",
    "from_weaver_frame",
    "from_weaver_routing_decision",
    "from_weaver_selectable_item",
    "infer_fastmcp_namespace",
    "infer_namespace",
    "load_a2a_session_jsonl",
    "load_fastmcp_catalog",
    "load_mcp_session_jsonl",
    "make_context_hook",
    "make_discovery_tool",
    "make_gateway_meta_tools",
    "make_proxy_meta_tools",
    "make_stripped_tools_list",
    "mcp_result_to_envelope",
    "mcp_tool_to_selectable",
    "to_weaver_choice_card",
    "to_weaver_choice_cards",
    "to_weaver_frame",
    "to_weaver_routing_decision",
    "to_weaver_selectable_item",
]

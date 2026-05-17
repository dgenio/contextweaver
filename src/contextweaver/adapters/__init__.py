"""Adapters sub-package for contextweaver.

Provides thin, pure stateless adapters across three responsibility groups:

1. **Protocol adapters** — convert external protocol data into
   contextweaver-native types and back: MCP (:mod:`.mcp`),
   FastMCP (:mod:`.fastmcp`), A2A (:mod:`.a2a`), and weaver-spec
   (:mod:`.weaver_contracts`).
2. **Runtime modes** — front upstream MCP servers as a transparent
   proxy or two-tool gateway: :mod:`.proxy_runtime`, :mod:`.mcp_proxy`,
   :mod:`.mcp_gateway`, :mod:`.mcp_proxy_server`,
   :mod:`.mcp_gateway_server`, :mod:`.mcp_upstream`.
3. **Provider-message ingestion** — one-call adoption from existing
   OpenAI / Anthropic / Gemini chat histories (issues #194, #219, #222):
   :mod:`.openai_messages`, :mod:`.anthropic_messages`,
   :mod:`.gemini_contents`. Each module ships a ``from_*`` decoder
   (plain provider dicts → ``ContextItem`` event-log entries) and a
   ``to_*`` inverse, with no provider SDK imported at module load time.

See ``AGENTS.md`` Module Map for the full per-file responsibility list.
"""

from __future__ import annotations

from contextweaver.adapters.a2a import (
    a2a_agent_to_selectable,
    a2a_result_to_envelope,
    load_a2a_session_jsonl,
)
from contextweaver.adapters.anthropic_messages import (
    from_anthropic_messages,
    to_anthropic_messages,
)
from contextweaver.adapters.fastmcp import (
    fastmcp_tool_to_selectable,
    fastmcp_tools_to_catalog,
    infer_fastmcp_namespace,
    load_fastmcp_catalog,
)
from contextweaver.adapters.gateway_error import GatewayError
from contextweaver.adapters.gemini_contents import (
    from_gemini_contents,
    to_gemini_contents,
)
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
from contextweaver.adapters.openai_messages import (
    from_openai_messages,
    to_openai_messages,
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
    "from_anthropic_messages",
    "from_gemini_contents",
    "from_openai_messages",
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
    "make_gateway_meta_tools",
    "make_proxy_meta_tools",
    "make_stripped_tools_list",
    "mcp_result_to_envelope",
    "mcp_tool_to_selectable",
    "to_anthropic_messages",
    "to_gemini_contents",
    "to_openai_messages",
    "to_weaver_choice_card",
    "to_weaver_choice_cards",
    "to_weaver_frame",
    "to_weaver_routing_decision",
    "to_weaver_selectable_item",
]

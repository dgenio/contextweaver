"""Adapters sub-package for contextweaver — thin, pure stateless adapters.

Three responsibility groups (see ``AGENTS.md`` Module Map for the full list):
1. **Protocol / source adapters** — convert external tool catalogs and
   protocol data into contextweaver-native types and back (MCP, FastMCP, A2A,
   weaver-spec, CrewAI, Pydantic AI, smolagents, Agno, LangChain, OpenAI Agents
   SDK, Google ADK, Microsoft Agent Framework, OpenAPI, Agent Skills); shared
   mechanics live in the private :mod:`._framework_common` helper (issue #454).
2. **Runtime modes** — front upstream MCP servers as a transparent proxy or
   two-tool gateway (``proxy_runtime`` / ``mcp_proxy`` / ``mcp_gateway`` + bindings).
3. **Provider-message ingestion** — one-call ``from_*`` / ``to_*`` adoption from
   OpenAI / Anthropic / Gemini chat histories; no provider SDK imported at load.
"""

from __future__ import annotations

from contextweaver.adapters._sidecar_http import make_sidecar_server, serve_api
from contextweaver.adapters.a2a import (
    a2a_agent_to_selectable,
    a2a_result_to_envelope,
    load_a2a_session_jsonl,
)
from contextweaver.adapters.agent_framework import (
    agent_framework_tool_to_selectable,
    agent_framework_tools_to_catalog,
    from_agent_framework_thread,
    infer_agent_framework_namespace,
    load_agent_framework_catalog,
    selectable_from_agent_framework_tool,
)
from contextweaver.adapters.agent_skills import (
    SkillBodySource,
    load_skills_catalog,
    parse_skill_frontmatter,
    skill_to_selectable,
)
from contextweaver.adapters.agno import (
    agno_tool_to_selectable,
    agno_tools_to_catalog,
    from_agno_agent,
    from_agno_session,
    infer_agno_namespace,
    load_agno_catalog,
    selectable_from_agno_tool,
)
from contextweaver.adapters.anthropic_messages import (
    from_anthropic_messages,
    to_anthropic_messages,
)
from contextweaver.adapters.chainweaver import (
    chainweaver_flow_to_selectable,
    chainweaver_flows_to_catalog,
    load_chainweaver_export,
)
from contextweaver.adapters.crewai import (
    crewai_tool_to_selectable,
    crewai_tools_to_catalog,
    infer_crewai_namespace,
    load_crewai_catalog,
)
from contextweaver.adapters.fastmcp import (
    fastmcp_tool_to_selectable,
    fastmcp_tools_to_catalog,
    infer_fastmcp_namespace,
    load_fastmcp_catalog,
    make_context_hook,
    make_discovery_tool,
)
from contextweaver.adapters.gateway_args import Repair, normalize_args
from contextweaver.adapters.gateway_controls import (
    RateLimiter,
    ToolResultCache,
    call_with_retry,
)
from contextweaver.adapters.gateway_error import (
    GatewayError,
    classify_upstream_exception,
    redact_upstream_detail,
)
from contextweaver.adapters.gateway_policy import (
    DryRunReport,
    RateLimit,
    RateLimitPolicy,
    RetryPolicy,
)
from contextweaver.adapters.gateway_validation import (
    CatalogRefreshReport,
    SchemaFinding,
    SchemaLimits,
    SkippedTool,
    check_schema_health,
)
from contextweaver.adapters.gemini_contents import (
    from_gemini_contents,
    to_gemini_contents,
)
from contextweaver.adapters.google_adk import (
    from_google_adk_session,
    google_adk_tool_to_selectable,
    google_adk_tools_to_catalog,
    infer_google_adk_namespace,
    load_google_adk_catalog,
    selectable_from_google_adk_tool,
)
from contextweaver.adapters.langchain import (
    infer_langchain_namespace,
    langchain_tool_to_selectable,
    langchain_tools_to_catalog,
    load_langchain_catalog,
    selectable_from_langchain_tool,
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
from contextweaver.adapters.openai_agents import (
    from_openai_agents_run,
    infer_openai_agents_namespace,
    load_openai_agents_catalog,
    openai_agents_tool_to_selectable,
    openai_agents_tools_to_catalog,
    selectable_from_openai_agents_tool,
)
from contextweaver.adapters.openai_messages import (
    from_openai_messages,
    to_openai_messages,
)
from contextweaver.adapters.openapi import (
    infer_openapi_namespace,
    load_openapi_catalog,
    openapi_operation_to_selectable,
    openapi_spec_to_catalog,
)
from contextweaver.adapters.proxy_runtime import ExposureMode, ProxyRuntime, UpstreamCall
from contextweaver.adapters.pydantic_ai import (
    from_pydantic_ai_messages,
    infer_pydantic_ai_namespace,
    load_pydantic_ai_catalog,
    pydantic_ai_tool_to_selectable,
    pydantic_ai_tools_to_catalog,
    selectable_from_pydantic_tool,
    to_pydantic_ai_messages,
)
from contextweaver.adapters.sidecar import SidecarApp, SidecarConfig
from contextweaver.adapters.sidecar_contract import (
    SIDECAR_API_VERSION,
    CompactRequest,
    CompactResponse,
    RouteRequest,
    RouteResponse,
    SidecarError,
)
from contextweaver.adapters.smolagents import (
    from_smolagents_agent,
    infer_smolagents_namespace,
    load_smolagents_catalog,
    selectable_from_smolagents_tool,
    smolagents_tool_to_selectable,
    smolagents_tools_to_catalog,
)
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
    "SIDECAR_API_VERSION",
    "CatalogRefreshReport",
    "CompactRequest",
    "CompactResponse",
    "DryRunReport",
    "ExposureMode",
    "GATEWAY_TOOL_NAMES",
    "GatewayError",
    "McpClientUpstream",
    "MultiplexUpstream",
    "PROXY_META_TOOL_NAMES",
    "ProxyRuntime",
    "RateLimit",
    "RateLimitPolicy",
    "RateLimiter",
    "Repair",
    "RetryPolicy",
    "RouteRequest",
    "RouteResponse",
    "SchemaFinding",
    "SchemaLimits",
    "SidecarApp",
    "SidecarConfig",
    "SidecarError",
    "SkillBodySource",
    "SkippedTool",
    "StubUpstream",
    "ToolResultCache",
    "UpstreamCall",
    "call_with_retry",
    "make_sidecar_server",
    "serve_api",
    "a2a_agent_to_selectable",
    "a2a_result_to_envelope",
    "agent_framework_tool_to_selectable",
    "agent_framework_tools_to_catalog",
    "agno_tool_to_selectable",
    "agno_tools_to_catalog",
    "chainweaver_flow_to_selectable",
    "chainweaver_flows_to_catalog",
    "check_schema_health",
    "classify_upstream_exception",
    "crewai_tool_to_selectable",
    "crewai_tools_to_catalog",
    "dispatch_meta_tool",
    "dispatch_proxy_request",
    "fastmcp_tool_to_selectable",
    "fastmcp_tools_to_catalog",
    "from_agent_framework_thread",
    "from_agno_agent",
    "from_agno_session",
    "from_anthropic_messages",
    "from_gemini_contents",
    "from_google_adk_session",
    "from_openai_agents_run",
    "from_openai_messages",
    "from_pydantic_ai_messages",
    "from_smolagents_agent",
    "from_weaver_choice_card",
    "from_weaver_choice_card_single",
    "from_weaver_frame",
    "from_weaver_routing_decision",
    "from_weaver_selectable_item",
    "infer_agent_framework_namespace",
    "infer_agno_namespace",
    "infer_crewai_namespace",
    "infer_fastmcp_namespace",
    "infer_google_adk_namespace",
    "infer_langchain_namespace",
    "infer_namespace",
    "infer_openai_agents_namespace",
    "infer_openapi_namespace",
    "infer_pydantic_ai_namespace",
    "infer_smolagents_namespace",
    "google_adk_tool_to_selectable",
    "google_adk_tools_to_catalog",
    "langchain_tool_to_selectable",
    "langchain_tools_to_catalog",
    "load_a2a_session_jsonl",
    "load_agent_framework_catalog",
    "load_agno_catalog",
    "load_chainweaver_export",
    "load_crewai_catalog",
    "load_fastmcp_catalog",
    "load_google_adk_catalog",
    "load_langchain_catalog",
    "load_mcp_session_jsonl",
    "load_openai_agents_catalog",
    "load_openapi_catalog",
    "load_pydantic_ai_catalog",
    "load_skills_catalog",
    "load_smolagents_catalog",
    "make_context_hook",
    "make_discovery_tool",
    "make_gateway_meta_tools",
    "make_proxy_meta_tools",
    "make_stripped_tools_list",
    "mcp_result_to_envelope",
    "mcp_tool_to_selectable",
    "normalize_args",
    "openai_agents_tool_to_selectable",
    "openai_agents_tools_to_catalog",
    "openapi_operation_to_selectable",
    "openapi_spec_to_catalog",
    "parse_skill_frontmatter",
    "pydantic_ai_tool_to_selectable",
    "pydantic_ai_tools_to_catalog",
    "redact_upstream_detail",
    "selectable_from_agent_framework_tool",
    "selectable_from_agno_tool",
    "selectable_from_google_adk_tool",
    "selectable_from_langchain_tool",
    "selectable_from_openai_agents_tool",
    "selectable_from_pydantic_tool",
    "selectable_from_smolagents_tool",
    "skill_to_selectable",
    "smolagents_tool_to_selectable",
    "smolagents_tools_to_catalog",
    "to_anthropic_messages",
    "to_gemini_contents",
    "to_openai_messages",
    "to_pydantic_ai_messages",
    "to_weaver_choice_card",
    "to_weaver_choice_cards",
    "to_weaver_frame",
    "to_weaver_routing_decision",
    "to_weaver_selectable_item",
]

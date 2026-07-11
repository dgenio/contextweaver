"""Serve episodic/fact memory stores as a standalone MCP server (issue #632).

``memory serve`` exposes any :class:`~contextweaver.store.protocols.EpisodicStore`
/ :class:`~contextweaver.store.protocols.FactStore` pair — in-memory, SQLite,
or an ``extras/memory`` backend — as four MCP tools so agents can read and
write long-lived memory over a standard transport:

- ``memory_add_episode(episode_id?, summary, metadata?)``
- ``memory_search_episodes(query, limit=5)``
- ``memory_put_fact(fact_id?, key, value, metadata?)``
- ``memory_get_facts(key?)``

Errors use the gateway's :class:`~contextweaver.adapters.gateway_error.GatewayError`
wire shape via :func:`~contextweaver.adapters.mcp_gateway.envelope_call_result`
(``ARGS_INVALID`` for malformed arguments); tools never raise across the MCP
boundary.  IDs are deterministic: a caller-supplied id wins, otherwise a
content hash is minted (mirroring :mod:`contextweaver.context.consolidation`).

**Sensitivity note:** this surface returns stored memory *verbatim* — the
operator chooses which stores to mount.  Pass ``redact_secrets=True`` to
scrub secret shapes from every read path via the shared
:mod:`contextweaver.secrets` helpers (house rule: the scrub call lives in a
shared helper, never a per-path copy — ``.claude/rules/sensitivity.md``).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, cast

from mcp import types as mcp_types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from contextweaver.adapters.gateway_error import GatewayError
from contextweaver.adapters.mcp_gateway import envelope_call_result
from contextweaver.secrets import scrub_secrets, scrub_secrets_in_obj
from contextweaver.store.episodic import Episode
from contextweaver.store.facts import Fact
from contextweaver.store.protocols import EpisodicStore, FactStore

logger = logging.getLogger("contextweaver.adapters.memory_server")

MEMORY_ADD_EPISODE = "memory_add_episode"
MEMORY_SEARCH_EPISODES = "memory_search_episodes"
MEMORY_PUT_FACT = "memory_put_fact"
MEMORY_GET_FACTS = "memory_get_facts"

MEMORY_TOOL_NAMES = (MEMORY_ADD_EPISODE, MEMORY_SEARCH_EPISODES, MEMORY_PUT_FACT, MEMORY_GET_FACTS)

_DEFAULT_SEARCH_LIMIT = 5
_MAX_SEARCH_LIMIT = 50


def _tool_def(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    """Build one MCP tool definition with a closed object schema."""
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return {"name": name, "description": description, "inputSchema": schema}


_STR = {"type": "string"}
_OBJ = {"type": "object"}


def make_memory_meta_tools() -> list[dict[str, Any]]:
    """Return the four MCP tool definitions for the memory server."""
    return [
        _tool_def(
            MEMORY_ADD_EPISODE,
            "Store one episodic memory (a compressed summary of a past task or "
            "conversation). Omit episode_id to mint a deterministic content-hash "
            "id; re-adding an existing id is a no-op.",
            {"episode_id": _STR, "summary": _STR, "metadata": _OBJ},
            ["summary"],
        ),
        _tool_def(
            MEMORY_SEARCH_EPISODES,
            "Search stored episodes by relevance to a free-text query.",
            {
                "query": _STR,
                "limit": {"type": "integer", "minimum": 1, "maximum": _MAX_SEARCH_LIMIT},
            },
            ["query"],
        ),
        _tool_def(
            MEMORY_PUT_FACT,
            "Insert or replace one key/value memory fact. Omit fact_id to mint "
            "a deterministic content-hash id (idempotent upsert).",
            {"fact_id": _STR, "key": _STR, "value": _STR, "metadata": _OBJ},
            ["key", "value"],
        ),
        _tool_def(
            MEMORY_GET_FACTS,
            "Return stored facts, optionally filtered by exact key.",
            {"key": _STR},
            [],
        ),
    ]


def _content_id(prefix: str, *parts: str) -> str:
    """Mint a deterministic, content-addressed id (caller-supplied ids win)."""
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{digest}"


def _args_error(message: str, *, label: str) -> dict[str, Any]:
    """Return the gateway ``ARGS_INVALID`` wire shape as a CallToolResult dict."""
    return envelope_call_result(GatewayError(code="ARGS_INVALID", message=message), label=label)


def _render_episode(episode: Episode, *, redact_secrets: bool) -> dict[str, Any]:
    """Serialise one episode for egress, scrubbing when configured (#428)."""
    summary = scrub_secrets(episode.summary) if redact_secrets else episode.summary
    metadata = dict(episode.metadata)
    if redact_secrets:
        metadata = cast(dict[str, Any], scrub_secrets_in_obj(metadata))
    return {"episode_id": episode.episode_id, "summary": summary, "metadata": metadata}


def _render_fact(fact: Fact, *, redact_secrets: bool) -> dict[str, Any]:
    """Serialise one fact for egress, scrubbing when configured (#428)."""
    value = scrub_secrets(fact.value) if redact_secrets else fact.value
    metadata = dict(fact.metadata)
    if redact_secrets:
        metadata = cast(dict[str, Any], scrub_secrets_in_obj(metadata))
    return {"fact_id": fact.fact_id, "key": fact.key, "value": value, "metadata": metadata}


def _opt_metadata(args: dict[str, Any]) -> dict[str, Any] | None:
    """Return the optional ``metadata`` object, or ``None`` when malformed."""
    metadata = args.get("metadata", {})
    return metadata if isinstance(metadata, dict) else None


async def dispatch_memory_tool(
    episodic: EpisodicStore,
    facts: FactStore,
    name: str,
    args: dict[str, Any],
    *,
    redact_secrets: bool = False,
) -> dict[str, Any]:
    """Invoke one memory tool by *name* and return an MCP CallToolResult dict.

    *args* comes off the MCP wire (already JSON-decoded).  With
    ``redact_secrets=True`` all read-path output (summaries, fact values,
    metadata) is scrubbed via :func:`contextweaver.secrets.scrub_secrets`.
    Errors carry the gateway's §3.4 :class:`GatewayError` shape with
    ``isError=True``; this function never raises across the MCP boundary.
    """
    if name == MEMORY_ADD_EPISODE:
        summary = args.get("summary")
        episode_id = args.get("episode_id")
        metadata = _opt_metadata(args)
        if not isinstance(summary, str) or not summary.strip():
            return _args_error(f"{name} requires a non-empty string 'summary'.", label=name)
        if (episode_id is not None and not isinstance(episode_id, str)) or metadata is None:
            return _args_error(
                f"{name} takes optional string 'episode_id' and object 'metadata'.", label=name
            )
        episode_id = episode_id or _content_id("episode", summary)
        if episodic.get(episode_id) is not None:
            return envelope_call_result({"episode_id": episode_id, "status": "exists"}, label=name)
        episodic.add(Episode(episode_id=episode_id, summary=summary, metadata=metadata))
        logger.debug("memory_server: added episode %s", episode_id)
        return envelope_call_result({"episode_id": episode_id, "status": "added"}, label=name)
    if name == MEMORY_SEARCH_EPISODES:
        query = args.get("query")
        limit = args.get("limit", _DEFAULT_SEARCH_LIMIT)
        if not isinstance(query, str) or not query.strip():
            return _args_error(f"{name} requires a non-empty string 'query'.", label=name)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not (1 <= limit <= _MAX_SEARCH_LIMIT)
        ):
            return _args_error(
                f"{name} 'limit' must be an integer in [1, {_MAX_SEARCH_LIMIT}].", label=name
            )
        hits = episodic.search(query, top_k=limit)
        payload = [_render_episode(ep, redact_secrets=redact_secrets) for ep in hits]
        return envelope_call_result(payload, label=name)
    if name == MEMORY_PUT_FACT:
        key, value = args.get("key"), args.get("value")
        fact_id = args.get("fact_id")
        metadata = _opt_metadata(args)
        if not isinstance(key, str) or not key.strip() or not isinstance(value, str):
            return _args_error(
                f"{name} requires a non-empty string 'key' and string 'value'.", label=name
            )
        if (fact_id is not None and not isinstance(fact_id, str)) or metadata is None:
            return _args_error(
                f"{name} takes optional string 'fact_id' and object 'metadata'.", label=name
            )
        fact_id = fact_id or _content_id("fact", key, value)
        facts.put(Fact(fact_id=fact_id, key=key, value=value, metadata=metadata))
        logger.debug("memory_server: stored fact %s", fact_id)
        return envelope_call_result({"fact_id": fact_id, "status": "stored"}, label=name)
    if name == MEMORY_GET_FACTS:
        key = args.get("key")
        if key is not None and not isinstance(key, str):
            return _args_error(f"{name} 'key' must be a string when supplied.", label=name)
        found = facts.get_by_key(key) if key is not None else facts.all()
        payload = [_render_fact(f, redact_secrets=redact_secrets) for f in found]
        return envelope_call_result(payload, label=name)
    return _args_error(
        f"unknown memory tool {name!r} (valid: {list(MEMORY_TOOL_NAMES)})", label=name
    )


def build_memory_server(
    episodic: EpisodicStore,
    facts: FactStore,
    *,
    server_name: str = "contextweaver-memory",
    redact_secrets: bool = False,
) -> Server[Any, Any]:
    """Build an :class:`mcp.server.Server` exposing the four memory tools.

    Args:
        episodic: Backing episodic store.
        facts: Backing fact store.
        server_name: MCP display name advertised in initialization.
        redact_secrets: Scrub secret shapes from read-path output (#428).

    Returns:
        A ready-to-run server (bind via :func:`run_memory_server_stdio` or the
        SDK's other transports).
    """
    server: Server[Any, Any] = Server(server_name)

    async def handle_list_tools() -> list[mcp_types.Tool]:
        return [
            mcp_types.Tool(
                name=tool["name"],
                description=tool["description"],
                inputSchema=tool["inputSchema"],
            )
            for tool in make_memory_meta_tools()
        ]

    async def handle_call_tool(
        name: str, arguments: dict[str, Any] | None
    ) -> mcp_types.CallToolResult:
        result = await dispatch_memory_tool(
            episodic, facts, name, arguments or {}, redact_secrets=redact_secrets
        )
        content: list[mcp_types.ContentBlock] = [
            mcp_types.TextContent(type="text", text=part.get("text", ""))
            for part in result.get("content", [])
            if part.get("type") == "text"
        ]
        return mcp_types.CallToolResult(content=content, isError=bool(result.get("isError")))

    # Call the decorators as functions (mirrors mcp_gateway_server) to avoid
    # the MCP SDK's untyped-decorator Any propagation under mypy --strict.
    cast(Any, server).list_tools()(handle_list_tools)
    cast(Any, server).call_tool()(handle_call_tool)
    return server


async def run_memory_server_stdio(
    episodic: EpisodicStore,
    facts: FactStore,
    *,
    server_name: str = "contextweaver-memory",
    redact_secrets: bool = False,
) -> None:
    """Run the memory server over stdio until the client disconnects.

    Mirrors :meth:`~contextweaver.adapters.mcp_gateway_server.McpGatewayServer.run_stdio`.
    """
    server = build_memory_server(
        episodic, facts, server_name=server_name, redact_secrets=redact_secrets
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


__all__ = [
    "MEMORY_TOOL_NAMES",
    "build_memory_server",
    "dispatch_memory_tool",
    "make_memory_meta_tools",
    "run_memory_server_stdio",
]

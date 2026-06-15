"""Concrete :class:`PrimitiveUpstream` adapters for resources and prompts (#669 / #670).

Sibling to :mod:`contextweaver.adapters.mcp_upstream` (which ships the three
*tool* upstream adapters): this module ships the matching trio for the
resource/prompt primitives that :class:`PrimitiveGatewayRuntime` consumes
(``docs/gateway_spec.md`` §9.4):

- :class:`StubPrimitiveUpstream` — in-process dict-shaped stub for tests,
  examples, the ``contextweaver mcp serve`` CLI, and air-gapped CI.
- :class:`McpClientPrimitiveUpstream` — wraps a single connected
  :class:`mcp.client.session.ClientSession`, fronting its
  ``resources/list`` / ``resources/read`` / ``prompts/list`` / ``prompts/get``
  endpoints.
- :class:`MultiplexPrimitiveUpstream` — fans listings out across several
  sources and routes reads/fetches back to the owning source.

Unlike the tool adapters (whose ``call_tool`` returns an ``isError`` dict),
these **raise** transport errors: the :class:`PrimitiveUpstream` Protocol
contract is that the runtime classifies failures via
:func:`~contextweaver.adapters.gateway_error.classify_upstream_exception`, so a
swallowed error here would hide the taxonomy code from the gateway.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("contextweaver.adapters.mcp_primitive_upstream")


class StubPrimitiveUpstream:
    """An in-process resource/prompt upstream wired up entirely from dicts.

    Useful for unit tests, examples, air-gapped CI, and the ``mcp serve`` CLI
    when no live upstream is attached.  The caller supplies static resource and
    prompt listings plus optional handlers that map a read/fetch to an MCP
    result dict; when a handler is omitted a deterministic canned result is
    returned so the gateway is exercisable end-to-end.

    Args:
        resource_defs: MCP ``resources/list`` entries (``uri`` / ``name`` /
            ``mimeType`` / ``description``).
        prompt_defs: MCP ``prompts/list`` entries (``name`` / ``description`` /
            ``arguments``).
        read_handler: Optional async callable invoked by :meth:`read_resource`.
        get_handler: Optional async callable invoked by :meth:`get_prompt`.
    """

    def __init__(
        self,
        resource_defs: list[dict[str, Any]] | None = None,
        prompt_defs: list[dict[str, Any]] | None = None,
        *,
        read_handler: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
        get_handler: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self._resource_defs = [dict(r) for r in resource_defs or []]
        self._prompt_defs = [dict(p) for p in prompt_defs or []]
        self._read_handler = read_handler
        self._get_handler = get_handler

    async def list_resources(self) -> list[dict[str, Any]]:
        """Return the configured resource definitions."""
        return [dict(r) for r in self._resource_defs]

    async def read_resource(self, uri: str) -> dict[str, Any]:
        """Dispatch to the configured handler or return a canned text read."""
        if self._read_handler is not None:
            return await self._read_handler(uri)
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "text/plain",
                    "text": f"contextweaver stub upstream\nresource: {uri}",
                }
            ]
        }

    async def list_prompts(self) -> list[dict[str, Any]]:
        """Return the configured prompt definitions."""
        return [dict(p) for p in self._prompt_defs]

    async def get_prompt(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Dispatch to the configured handler or return a canned rendered prompt."""
        if self._get_handler is not None:
            return await self._get_handler(name, arguments)
        return {
            "description": f"stub prompt {name}",
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": f"contextweaver stub prompt {name}; args={sorted(arguments)}",
                    },
                }
            ],
        }


class McpClientPrimitiveUpstream:
    """Adapt a connected :class:`mcp.client.session.ClientSession` to :class:`PrimitiveUpstream`.

    The wrapped session must already be connected; lifecycle management
    (connect / close, transport, auth) is the caller's responsibility.  The MCP
    SDK's pydantic results are converted to plain dicts so downstream consumers
    keep operating on the MCP wire format.  Transport errors (including the
    :class:`TimeoutError` raised by the per-call timeout) propagate so the
    runtime can classify them.

    Args:
        session: A connected :class:`mcp.client.session.ClientSession`.
        timeout: Seconds before an upstream call is abandoned (raising
            :class:`TimeoutError`).  Defaults to 30 seconds; pass ``None`` to
            disable (not recommended for production deployments).
    """

    def __init__(
        self,
        session: Any,  # noqa: ANN401 — MCP SDK ClientSession
        *,
        timeout: float | None = 30.0,
    ) -> None:
        self._session = session
        self._timeout = timeout

    async def list_resources(self) -> list[dict[str, Any]]:
        """Call ``resources/list`` upstream and return dict-shaped defs."""
        listing = await asyncio.wait_for(self._session.list_resources(), timeout=self._timeout)
        return [_model_to_dict(r) for r in _unwrap_listing(listing, "resources")]

    async def read_resource(self, uri: str) -> dict[str, Any]:
        """Forward a ``resources/read`` and return a dict-shaped MCP result."""
        result = await asyncio.wait_for(self._session.read_resource(uri), timeout=self._timeout)
        return _model_to_dict(result)

    async def list_prompts(self) -> list[dict[str, Any]]:
        """Call ``prompts/list`` upstream and return dict-shaped defs."""
        listing = await asyncio.wait_for(self._session.list_prompts(), timeout=self._timeout)
        return [_model_to_dict(p) for p in _unwrap_listing(listing, "prompts")]

    async def get_prompt(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Forward a ``prompts/get`` and return a dict-shaped MCP result."""
        result = await asyncio.wait_for(
            self._session.get_prompt(name, arguments), timeout=self._timeout
        )
        return _model_to_dict(result)


class MultiplexPrimitiveUpstream:
    """Fan resource/prompt listings across multiple :class:`PrimitiveUpstream` sources.

    :meth:`read_resource` routes by the URI that exported it; :meth:`get_prompt`
    routes by the prompt name.  When two upstreams export the same URI/name, the
    first source registered wins — the §9 cross-primitive identity policy keeps
    this collision rare in practice.  Routing indices are populated lazily by the
    corresponding ``list_*`` call, so callers should list before reading (the
    :class:`PrimitiveGatewayRuntime` always refreshes before serving).  Each
    ``list_*`` rebuilds its ownership index from scratch, so repeated listings
    (e.g. successive ``PrimitiveGatewayRuntime.refresh()`` calls) are idempotent.
    """

    def __init__(self, sources: list[Any]) -> None:  # noqa: ANN401 — PrimitiveUpstream Protocol
        self._sources = list(sources)
        self._resource_owner: dict[str, int] = {}
        self._prompt_owner: dict[str, int] = {}

    async def list_resources(self) -> list[dict[str, Any]]:
        """Return the union of resource defs across all sources."""
        self._resource_owner.clear()
        out: list[dict[str, Any]] = []
        for idx, source in enumerate(self._sources):
            for resource_def in await source.list_resources():
                uri = str(resource_def.get("uri", ""))
                if uri and uri not in self._resource_owner:
                    self._resource_owner[uri] = idx
                    out.append(resource_def)
        return out

    async def list_prompts(self) -> list[dict[str, Any]]:
        """Return the union of prompt defs across all sources."""
        self._prompt_owner.clear()
        out: list[dict[str, Any]] = []
        for idx, source in enumerate(self._sources):
            for prompt_def in await source.list_prompts():
                name = str(prompt_def.get("name", ""))
                if name and name not in self._prompt_owner:
                    self._prompt_owner[name] = idx
                    out.append(prompt_def)
        return out

    async def read_resource(self, uri: str) -> dict[str, Any]:
        """Forward the read to the source that owns *uri*."""
        idx = self._resource_owner.get(uri)
        if idx is None:
            raise LookupError(f"no upstream owns resource {uri!r}")
        result: dict[str, Any] = await self._sources[idx].read_resource(uri)
        return result

    async def get_prompt(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Forward the fetch to the source that owns prompt *name*."""
        idx = self._prompt_owner.get(name)
        if idx is None:
            raise LookupError(f"no upstream owns prompt {name!r}")
        result: dict[str, Any] = await self._sources[idx].get_prompt(name, arguments)
        return result


def _unwrap_listing(listing: Any, key: str) -> list[Any]:  # noqa: ANN401
    """Extract the entry list from a ``*/list`` result of any shape.

    The MCP SDK returns a pydantic result object carrying the entries under
    *key* (``.resources`` / ``.prompts``); a dict-shaped payload nests them the
    same way (``{"resources": [...]}``); a bare list is already the entries.
    Unwrapping explicitly avoids iterating a dict's string keys into
    :func:`_model_to_dict`.
    """
    if isinstance(listing, dict):
        entries = listing.get(key)
    elif isinstance(listing, list):
        entries = listing
    else:
        entries = getattr(listing, key, None)
    return list(entries) if entries else []


def _model_to_dict(obj: Any) -> dict[str, Any]:  # noqa: ANN401
    """Coerce a possibly-pydantic MCP result/entry to a plain dict."""
    if isinstance(obj, dict):
        return dict(obj)
    if hasattr(obj, "model_dump"):
        return dict(obj.model_dump(mode="json"))
    return dict(vars(obj))

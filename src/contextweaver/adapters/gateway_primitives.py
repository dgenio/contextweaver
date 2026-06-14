"""Gateway runtime for MCP resources and prompts (#669 / #670).

Companion to :class:`~contextweaver.adapters.proxy_runtime.ProxyRuntime` (which
shapes *tools*): :class:`PrimitiveGatewayRuntime` extends the same
bounded-choice + firewall treatment to resources and prompts (#555).  They are
modelled as ``SelectableItem``\\s (``kind="resource"`` / ``"prompt"``) so they
reuse the routing ``Catalog`` / ``Router`` / ``ChoiceCard`` machinery; each kind
gets its own :class:`~contextweaver.adapters._primitive_index.PrimitiveIndex`
so browse results never mix, while a
shared :class:`~contextweaver.context.manager.ContextManager` keeps artifacts /
firewall / ``tool_view`` unified with the tool runtime.  Distinct verbs
(``resource_read`` / ``prompt_get``) match MCP's distinct semantics rather than
overloading ``tool_execute``.  See ``docs/gateway_spec.md`` §9; the transport
binding lives in :mod:`contextweaver.adapters.mcp_gateway_primitives`.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Protocol, runtime_checkable

import jsonschema.exceptions

from contextweaver.adapters._primitive_index import PrimitiveIndex
from contextweaver.adapters.gateway_error import GatewayError, classify_upstream_exception
from contextweaver.adapters.gateway_validation import build_validator
from contextweaver.adapters.mcp_primitives import (
    mcp_prompt_get_to_envelope,
    mcp_prompt_to_selectable,
    mcp_resource_read_to_envelope,
    mcp_resource_to_selectable,
)
from contextweaver.context.manager import ContextManager
from contextweaver.envelope import ChoiceCard, ResultEnvelope
from contextweaver.exceptions import CatalogError, ItemNotFoundError
from contextweaver.types import SelectableItem

logger = logging.getLogger("contextweaver.adapters.gateway_primitives")


@runtime_checkable
class PrimitiveUpstream(Protocol):
    """Transport-agnostic access to upstream MCP resources and prompts.

    Mirrors :class:`~contextweaver.adapters.proxy_runtime.UpstreamCall` for the
    resource/prompt primitives; transport errors are raised (the runtime
    classifies them), not returned as partial results.
    """

    async def list_resources(self) -> list[dict[str, Any]]:
        """Return MCP ``resources/list`` entries from all upstream servers."""
        ...

    async def read_resource(self, uri: str) -> dict[str, Any]:
        """Read the resource at *uri* and return the raw MCP result dict."""
        ...

    async def list_prompts(self) -> list[dict[str, Any]]:
        """Return MCP ``prompts/list`` entries from all upstream servers."""
        ...

    async def get_prompt(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Fetch prompt *name* with *arguments* and return the raw MCP result."""
        ...


class PrimitiveGatewayRuntime:
    """Bounded-choice gateway runtime for MCP resources and prompts.

    Args:
        upstream: A :class:`PrimitiveUpstream` implementation.
        context_manager: Shared :class:`ContextManager` (typically the tool
            :class:`ProxyRuntime`'s) so reads land in one artifact store /
            firewall and ``tool_view`` works across every primitive.
        beam_width: Router beam width for both indices.
        top_k: Max cards per browse.
    """

    def __init__(
        self,
        upstream: PrimitiveUpstream,
        *,
        context_manager: ContextManager | None = None,
        beam_width: int = 3,
        top_k: int = 10,
    ) -> None:
        self._upstream = upstream
        self._context_manager = context_manager or ContextManager()
        self._resources = PrimitiveIndex(beam_width=beam_width, top_k=top_k)
        self._prompts = PrimitiveIndex(beam_width=beam_width, top_k=top_k)

    @property
    def context_manager(self) -> ContextManager:
        """Return the shared per-session :class:`ContextManager`."""
        return self._context_manager

    async def refresh(self) -> tuple[int, int]:
        """Re-fetch resources and prompts upstream; return ``(n_res, n_prompt)``."""
        resources = await self._upstream.list_resources()
        prompts = await self._upstream.list_prompts()
        return self.register_sync(resources, prompts)

    def register_sync(
        self, resource_defs: list[dict[str, Any]], prompt_defs: list[dict[str, Any]]
    ) -> tuple[int, int]:
        """Register raw resource/prompt defs synchronously (tests/demos)."""
        n_res = self._resources.rebuild(_convert(resource_defs, mcp_resource_to_selectable))
        n_prompt = self._prompts.rebuild(_convert(prompt_defs, mcp_prompt_to_selectable))
        logger.debug("primitive runtime: registered %d resources, %d prompts", n_res, n_prompt)
        return n_res, n_prompt

    def browse_resources(
        self, *, query: str | None = None, path: str | None = None, top_k: int | None = None
    ) -> list[ChoiceCard] | GatewayError:
        """Browse the resource catalog (``resource_browse``)."""
        return self._resources.browse(query=query, path=path, top_k=top_k)

    def browse_prompts(
        self, *, query: str | None = None, path: str | None = None, top_k: int | None = None
    ) -> list[ChoiceCard] | GatewayError:
        """Browse the prompt catalog (``prompt_browse``)."""
        return self._prompts.browse(query=query, path=path, top_k=top_k)

    async def read_resource(self, resource_id: str) -> ResultEnvelope | GatewayError:
        """Read a resource by canonical id and firewall the result (``resource_read``)."""
        try:
            item = self._resources.catalog.get(resource_id)
        except ItemNotFoundError as exc:
            return GatewayError(code="RESOURCE_NOT_FOUND", message=str(exc), path=resource_id)
        uri = str(item.metadata.get("uri", ""))
        try:
            raw = await self._upstream.read_resource(uri)
        except Exception as exc:  # noqa: BLE001 — classify, never raise across the boundary
            code, retryable = classify_upstream_exception(exc)
            logger.warning("primitive runtime: read_resource %s failed: %r", resource_id, exc)
            return GatewayError(
                code=code, message="resource read failed", path=resource_id, retryable=retryable
            )
        envelope, binaries, full_text = mcp_resource_read_to_envelope(raw, resource_id)
        self._persist(envelope, binaries, full_text, handle_stub=f"resource:{resource_id}")
        return envelope

    async def get_prompt(
        self, prompt_id: str, arguments: dict[str, Any]
    ) -> ResultEnvelope | GatewayError:
        """Fetch a prompt by canonical id, validating arguments (``prompt_get``)."""
        try:
            item = self._prompts.catalog.get(prompt_id)
        except ItemNotFoundError as exc:
            return GatewayError(code="PROMPT_NOT_FOUND", message=str(exc), path=prompt_id)
        invalid = _validate(item.args_schema, arguments, prompt_id)
        if invalid is not None:
            return invalid
        upstream_name = str(item.metadata.get("prompt_name", item.name))
        try:
            raw = await self._upstream.get_prompt(upstream_name, arguments)
        except Exception as exc:  # noqa: BLE001 — classify, never raise across the boundary
            code, retryable = classify_upstream_exception(exc)
            logger.warning("primitive runtime: get_prompt %s failed: %r", prompt_id, exc)
            return GatewayError(
                code=code, message="prompt fetch failed", path=prompt_id, retryable=retryable
            )
        envelope, binaries, full_text = mcp_prompt_get_to_envelope(raw, prompt_id)
        self._persist(envelope, binaries, full_text, handle_stub=f"prompt:{prompt_id}")
        return envelope

    def _persist(
        self,
        envelope: ResultEnvelope,
        binaries: dict[str, tuple[bytes, str, str]],
        full_text: str,
        *,
        handle_stub: str,
    ) -> None:
        """Persist extracted binaries + full text on the shared artifact store."""
        store = self._context_manager.artifact_store
        for handle, (data, mime, label) in binaries.items():
            if not store.exists(handle):
                store.put(handle=handle, content=data, media_type=mime, label=label)
        if full_text:
            text_hash = hashlib.sha256(full_text.encode("utf-8")).hexdigest()[:16]
            text_handle = f"{handle_stub}:text:{text_hash}"
            if not store.exists(text_handle):
                store.put(
                    handle=text_handle,
                    content=full_text.encode("utf-8"),
                    media_type="text/plain",
                    label=handle_stub,
                )


def _convert(defs: list[dict[str, Any]], converter: Any) -> list[SelectableItem]:  # noqa: ANN401
    """Convert *defs* with *converter*, skipping malformed entries defensively."""
    items: list[SelectableItem] = []
    seen: set[str] = set()
    for index, raw in enumerate(defs):
        try:
            item = converter(raw)
        except (CatalogError, KeyError, TypeError, ValueError) as exc:
            logger.warning("primitive runtime: skipping malformed def at %d: %r", index, exc)
            continue
        if item.id in seen:  # deterministic de-dup: first occurrence wins
            continue
        seen.add(item.id)
        items.append(item)
    return items


def _validate(schema: dict[str, Any], args: dict[str, Any], path: str) -> GatewayError | None:
    """Validate *args* against *schema*; return ``ARGS_INVALID`` on failure."""
    if not schema or not schema.get("properties") and not schema.get("required"):
        return None
    try:
        build_validator(schema).validate(args)
    except jsonschema.exceptions.SchemaError as exc:
        return GatewayError(code="SCHEMA_INVALID", message=str(exc), path=path)
    except jsonschema.exceptions.ValidationError as exc:
        return GatewayError(
            code="ARGS_INVALID", message=exc.message, path=path, details={"path": list(exc.path)}
        )
    return None

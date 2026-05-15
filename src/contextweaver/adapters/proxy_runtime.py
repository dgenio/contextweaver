"""Shared core for the MCP proxy (#13) and gateway (#28) runtime modes.

:class:`ProxyRuntime` is the internal subsystem that both the transparent
proxy and the two-tool gateway share, as called for by ``docs/gateway_spec.md``
§4.  It owns:

- Upstream MCP catalog aggregation via an injected
  :class:`UpstreamCall` Protocol (no MCP transport coupling).
- A per-session :class:`~contextweaver.context.manager.ContextManager`
  with the standard 4-store bundle.
- A :class:`~contextweaver.routing.catalog.Catalog` +
  :class:`~contextweaver.routing.graph.ChoiceGraph` rebuilt whenever the
  upstream catalog changes.
- The gateway's three primitives:

  * :meth:`ProxyRuntime.browse` — ``tool_browse(query|path)`` per §3.
  * :meth:`ProxyRuntime.execute` — ``tool_execute(tool_id, args)`` per
    §4.4 (validates args against the hydrated schema before upstream
    dispatch, then runs the result through the context firewall).
  * :meth:`ProxyRuntime.view` — ``tool_view(handle, selector)`` per #34
    (drilldown over a previously-stored artifact).

- The proxy's additional helpers:

  * :meth:`ProxyRuntime.strip_tools_list` — stripped MCP-format
    ``tools/list`` per §4.1.
  * :meth:`ProxyRuntime.hydrate` — `Catalog.hydrate` wrapper exposed as
    ``tool_hydrate(tool_id)`` per §4.1.

The shared error shape is :class:`~contextweaver.adapters.gateway_error.GatewayError`;
none of these primitives raise across the MCP boundary.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

import jsonschema
import jsonschema.exceptions

from contextweaver.adapters.gateway_error import GatewayError
from contextweaver.adapters.mcp import mcp_result_to_envelope, mcp_tool_to_selectable
from contextweaver.context.manager import ContextManager
from contextweaver.envelope import ChoiceCard, HydrationResult, ResultEnvelope
from contextweaver.exceptions import (
    ArtifactNotFoundError,
    ContextWeaverError,
    ItemNotFoundError,
    PathInvalidError,
    PathNotFoundError,
)
from contextweaver.routing.cards import bound_browse_response, item_to_card, make_choice_cards
from contextweaver.routing.catalog import Catalog
from contextweaver.routing.graph import ChoiceGraph
from contextweaver.routing.path import parse_path, resolve_path
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.types import SelectableItem

logger = logging.getLogger("contextweaver.adapters.proxy_runtime")


class ExposureMode(str, Enum):
    """Which agent-facing surface the runtime is wired into (§4)."""

    TRANSPARENT = "transparent"
    GATEWAY = "gateway"


@runtime_checkable
class UpstreamCall(Protocol):
    """Transport-agnostic interface to one or more upstream MCP servers.

    Implementations may fan out over a single server, multiple servers,
    or an in-process stub.  The :class:`ProxyRuntime` only depends on
    the two methods below — concrete MCP-SDK wiring lives in
    :mod:`contextweaver.adapters.mcp_upstream`.
    """

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return MCP-format tool definitions from all upstream servers."""
        ...

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke *tool_name* upstream and return the raw MCP result dict.

        The returned dict matches the MCP wire format consumed by
        :func:`~contextweaver.adapters.mcp.mcp_result_to_envelope`.
        Implementations MUST translate transport-level errors into a
        result dict with ``isError = True`` rather than raising.
        """
        ...


@dataclass
class _UpstreamNameIndex:
    """Maps canonical ``tool_id`` → upstream raw tool name.

    Required because :func:`mcp_tool_to_selectable` strips namespace
    prefixes from the canonical id (§1.4), but the upstream MCP server
    only accepts the original name.
    """

    by_tool_id: dict[str, str] = field(default_factory=dict)


class ProxyRuntime:
    """Shared core for the MCP proxy and gateway modes.

    Args:
        upstream: An :class:`UpstreamCall` implementation.
        mode: Which agent-facing surface this runtime serves.  Defaults
            to :attr:`ExposureMode.GATEWAY`.
        context_manager: Optional pre-built
            :class:`~contextweaver.context.manager.ContextManager`.
            Defaults to a fresh one with the standard 4-store bundle.
        tree_builder: Optional custom
            :class:`~contextweaver.routing.tree.TreeBuilder`.
        beam_width: Router beam width passed through to
            :class:`~contextweaver.routing.router.Router`.
        top_k: Maximum number of cards returned by :meth:`browse`.
    """

    def __init__(
        self,
        upstream: UpstreamCall,
        *,
        mode: ExposureMode = ExposureMode.GATEWAY,
        context_manager: ContextManager | None = None,
        tree_builder: TreeBuilder | None = None,
        beam_width: int = 3,
        top_k: int = 10,
    ) -> None:
        self._upstream = upstream
        self._mode = mode
        self._context_manager = context_manager or ContextManager()
        self._tree_builder = tree_builder or TreeBuilder()
        self._beam_width = beam_width
        self._top_k = top_k
        self._catalog: Catalog = Catalog()
        self._graph: ChoiceGraph | None = None
        self._router: Router | None = None
        self._upstream_names = _UpstreamNameIndex()
        self._raw_tool_defs: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def mode(self) -> ExposureMode:
        """Return the configured :class:`ExposureMode`."""
        return self._mode

    @property
    def catalog(self) -> Catalog:
        """Return the active :class:`Catalog`."""
        return self._catalog

    @property
    def context_manager(self) -> ContextManager:
        """Return the per-session :class:`ContextManager`."""
        return self._context_manager

    # ------------------------------------------------------------------
    # Catalog management
    # ------------------------------------------------------------------

    async def refresh_catalog(self) -> int:
        """Re-fetch the upstream ``tools/list`` and rebuild the catalog.

        Returns:
            The number of tools registered.
        """
        tool_defs = await self._upstream.list_tools()
        return self._register_tool_defs(tool_defs)

    def register_tool_defs_sync(self, tool_defs: list[dict[str, Any]]) -> int:
        """Register *tool_defs* (MCP-format) into the catalog synchronously.

        Useful for tests and demos that want to bypass the async upstream
        boundary.  Returns the number of registered tools.
        """
        return self._register_tool_defs(tool_defs)

    def _register_tool_defs(self, tool_defs: list[dict[str, Any]]) -> int:
        items: list[SelectableItem] = []
        upstream_index: dict[str, str] = {}
        raw_defs: dict[str, dict[str, Any]] = {}
        for tool_def in tool_defs:
            item = mcp_tool_to_selectable(tool_def)
            items.append(item)
            upstream_index[item.id] = str(tool_def["name"])
            raw_defs[item.id] = dict(tool_def)
        self._catalog = Catalog()
        for item in items:
            self._catalog.register(item)
        self._upstream_names = _UpstreamNameIndex(by_tool_id=upstream_index)
        self._raw_tool_defs = raw_defs
        if items:
            self._graph = self._tree_builder.build(items)
            self._router = Router(
                self._graph,
                items=items,
                beam_width=self._beam_width,
                top_k=self._top_k,
            )
        else:
            self._graph = None
            self._router = None
        logger.debug("proxy_runtime: registered %d tools", len(items))
        return len(items)

    def list_tool_ids(self) -> list[str]:
        """Return the canonical ``tool_id`` for every registered tool."""
        return [item.id for item in self._catalog.all()]

    # ------------------------------------------------------------------
    # tool_browse (§3 + §4.2)
    # ------------------------------------------------------------------

    def browse(
        self,
        *,
        query: str | None = None,
        path: str | None = None,
        top_k: int | None = None,
    ) -> list[ChoiceCard] | GatewayError:
        """Implement ``tool_browse(query|path)`` per §3.1.

        Exactly one of *query* or *path* must be supplied — passing both
        or neither returns :class:`GatewayError` with code
        ``ARGS_INVALID``.

        Args:
            query: Free-form natural-language query.  Routed through
                :class:`Router`.
            path: Hierarchical path through the :class:`ChoiceGraph`.
            top_k: Optional override for the configured top-k count.

        Returns:
            A list of :class:`ChoiceCard` bounded per §2.3, or a
            :class:`GatewayError` describing why the request was
            rejected.
        """
        if (query is None) == (path is None):
            return GatewayError(
                code="ARGS_INVALID",
                message="tool_browse requires exactly one of 'query' or 'path'.",
            )
        if query is not None:
            return self._browse_by_query(query, top_k=top_k)
        assert path is not None  # narrowed by the XOR check above
        return self._browse_by_path(path)

    def _browse_by_query(self, query: str, *, top_k: int | None) -> list[ChoiceCard] | GatewayError:
        if self._router is None or not self._catalog.all():
            return []
        effective_top_k = top_k if top_k is not None else self._top_k
        # Router.top_k is set at construction to self._top_k; per-call
        # overrides are applied by truncating the result via make_choice_cards.
        # Values larger than self._top_k are silently capped at self._top_k.
        result = self._router.route(query)
        scores = dict(zip(result.candidate_ids, result.scores, strict=False))
        cards = make_choice_cards(
            result.candidate_items,
            max_cards=effective_top_k,
            scores=scores,
        )
        return bound_browse_response(cards)

    def _browse_by_path(self, path: str) -> list[ChoiceCard] | GatewayError:
        if self._graph is None:
            return GatewayError(
                code="PATH_NOT_FOUND",
                message="No catalog registered.",
                path=path,
            )
        try:
            segments = parse_path(path)
        except PathInvalidError as exc:
            return GatewayError(code="PATH_INVALID", message=str(exc), path=path)
        try:
            child_ids = resolve_path(self._graph, segments)
        except PathInvalidError as exc:
            return GatewayError(code="PATH_INVALID", message=str(exc), path=path)
        except PathNotFoundError as exc:
            return GatewayError(code="PATH_NOT_FOUND", message=str(exc), path=path)
        cards: list[ChoiceCard] = []
        for child_id in child_ids:
            try:
                cards.append(item_to_card(self._catalog.get(child_id)))
            except ItemNotFoundError:
                # Navigation node, not a leaf — synthesise a cluster card.
                node = self._graph.get_node(child_id)
                cards.append(
                    ChoiceCard(
                        id=child_id,
                        name=node.label or child_id,
                        description=node.routing_hint or "Cluster",
                        kind="internal",
                        namespace=child_id.split(":", 1)[0] if ":" in child_id else "",
                    )
                )
        return bound_browse_response(cards)

    # ------------------------------------------------------------------
    # tool_hydrate (§4.1)
    # ------------------------------------------------------------------

    def hydrate(self, tool_id: str) -> HydrationResult | GatewayError:
        """Return the full schema for *tool_id* (§4.3).

        Returns:
            A :class:`HydrationResult` or :class:`GatewayError` with code
            ``HYDRATE_FAILED`` when *tool_id* is unknown.
        """
        try:
            return self._catalog.hydrate(tool_id)
        except ItemNotFoundError as exc:
            return GatewayError(
                code="HYDRATE_FAILED",
                message=str(exc),
                path=tool_id,
            )

    # ------------------------------------------------------------------
    # tool_execute (§4.2 + §4.4)
    # ------------------------------------------------------------------

    async def execute(
        self,
        tool_id: str,
        args: dict[str, Any],
    ) -> ResultEnvelope | GatewayError:
        """Validate *args*, invoke upstream, and return a compacted envelope.

        Args:
            tool_id: Canonical ``tool_id`` of the target tool.
            args: Arguments to pass through to the upstream MCP server.

        Returns:
            A :class:`ResultEnvelope` (post-firewall) or a
            :class:`GatewayError`.  Validation failures map to
            ``ARGS_INVALID`` per §4.4; transport / protocol failures map
            to ``UPSTREAM_ERROR``.
        """
        try:
            hydrated = self._catalog.hydrate(tool_id)
        except ItemNotFoundError as exc:
            return GatewayError(
                code="HYDRATE_FAILED",
                message=str(exc),
                path=tool_id,
            )
        validation_error = _validate_args(args, hydrated.args_schema)
        if validation_error is not None:
            return GatewayError(
                code="ARGS_INVALID",
                message=validation_error.message,
                path=tool_id,
                details={"path": list(validation_error.path)},
            )
        upstream_name = self._upstream_names.by_tool_id.get(tool_id, hydrated.item.name)
        try:
            raw = await self._upstream.call_tool(upstream_name, args)
        except Exception as exc:  # noqa: BLE001
            return GatewayError(
                code="UPSTREAM_ERROR",
                message=f"upstream call failed: {exc}",
                path=tool_id,
            )
        envelope, binaries, full_text = mcp_result_to_envelope(raw, upstream_name)
        # Persist binaries on the session's artifact store so subsequent
        # tool_view calls can drill in.  Text content larger than the
        # firewall threshold is also persisted under a deterministic
        # handle so the gateway's view path can address it.
        artifact_store = self._context_manager.artifact_store
        for handle, (data, mime, label) in binaries.items():
            if not artifact_store.exists(handle):
                artifact_store.put(handle=handle, content=data, media_type=mime, label=label)
        if full_text and not envelope.artifacts:
            content_bytes = full_text.encode("utf-8")
            text_hash = hashlib.sha256(content_bytes).hexdigest()[:16]
            text_handle = f"text:{tool_id}:{text_hash}"
            if not artifact_store.exists(text_handle):
                artifact_store.put(
                    handle=text_handle,
                    content=content_bytes,
                    media_type="text/plain",
                    label=f"text result from {tool_id}",
                )
        envelope.provenance.setdefault("tool_id", tool_id)
        return envelope

    # ------------------------------------------------------------------
    # tool_view (#34)
    # ------------------------------------------------------------------

    def view(self, handle: str, selector: dict[str, Any]) -> str | GatewayError:
        """Drill into a previously stored artifact (#34).

        Returns the sliced text content, or a :class:`GatewayError` with
        code ``VIEW_FAILED`` if the handle is unknown or the selector is
        invalid.
        """
        store: InMemoryArtifactStore = self._context_manager.artifact_store  # type: ignore[assignment]
        try:
            return store.drilldown(handle, selector)
        except (ArtifactNotFoundError, ContextWeaverError) as exc:
            return GatewayError(
                code="VIEW_FAILED",
                message=str(exc),
                path=handle,
            )

    # ------------------------------------------------------------------
    # Proxy-only: stripped tools/list (§4.1)
    # ------------------------------------------------------------------

    def strip_tools_list(self) -> list[dict[str, Any]]:
        """Return the stripped ``tools/list`` (§4.1) for transparent-proxy mode.

        Each entry mirrors the upstream tool's *name* and *description*
        (description truncated to the §2.4 budget) but replaces
        ``inputSchema`` with the sentinel ``{"type": "object"}``.  No
        banned fields are emitted (§2.2).

        Returns:
            A list of MCP-format tool definitions ready for the proxy's
            ``tools/list`` response.
        """
        out: list[dict[str, Any]] = []
        for item in self._catalog.all():
            card = item_to_card(item)
            out.append(
                {
                    "name": item.id,
                    "description": card.description,
                    "inputSchema": {"type": "object"},
                }
            )
        return out


def _validate_args(
    args: dict[str, Any],
    schema: dict[str, Any],
) -> jsonschema.exceptions.ValidationError | None:
    """Validate *args* against *schema*; return ``None`` on success."""
    if not schema:
        return None
    try:
        jsonschema.validate(instance=args, schema=schema)
        return None
    except jsonschema.exceptions.ValidationError as exc:
        return exc

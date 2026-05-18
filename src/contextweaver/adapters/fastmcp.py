"""FastMCP adapter for contextweaver.

Bridges FastMCP servers and contextweaver :class:`~contextweaver.routing.catalog.Catalog`
objects.  Converts FastMCP tool definitions into
:class:`~contextweaver.types.SelectableItem` objects and provides live server
discovery via the FastMCP ``Client``.

Core conversion functions (:func:`fastmcp_tool_to_selectable`,
:func:`fastmcp_tools_to_catalog`) work with plain dicts — no ``fastmcp``
install required.  Live server discovery (:func:`load_fastmcp_catalog`)
requires the ``contextweaver[fastmcp]`` optional extra.

FastMCP CodeMode hooks (:func:`make_discovery_tool`, :func:`make_context_hook`)
return plain callables suitable for any runtime that supports custom
discovery / context hooks (FastMCP CodeMode, LangChain, LlamaIndex,
hand-rolled loops); no ``fastmcp`` import is needed on either the producer
or the consumer side of the returned callable.

FastMCP composition docs: https://gofastmcp.com/servers/composition
FastMCP CodeMode discussion: https://github.com/PrefectHQ/fastmcp/discussions/3365
"""

from __future__ import annotations

import copy
import logging
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from contextweaver.adapters.mcp import mcp_tool_to_selectable
from contextweaver.exceptions import CatalogError, ConfigError, ItemNotFoundError
from contextweaver.routing.catalog import Catalog
from contextweaver.types import SelectableItem

if TYPE_CHECKING:
    from contextweaver.context.manager import ContextManager
    from contextweaver.routing.router import Router

logger = logging.getLogger("contextweaver.adapters")

# ---------------------------------------------------------------------------
# Namespace inference (FastMCP-aware)
# ---------------------------------------------------------------------------

_FALLBACK_NS = "fastmcp"


def infer_fastmcp_namespace(tool_name: str) -> str:
    """Infer a namespace from a FastMCP-namespaced tool name.

    FastMCP's composition layer joins ``{namespace}_{toolname}`` with a single
    underscore separator (see https://gofastmcp.com/servers/composition).
    For dot- and slash-delimited names this function mirrors the prefix-extraction
    logic of :func:`~contextweaver.adapters.mcp.infer_namespace` (kept inline to
    avoid coupling on that function's fallback sentinel).  For underscore-delimited
    names it
    accepts **2+** segments (unlike the generic MCP heuristic which requires 3+),
    since 2-segment names like ``github_search`` are normal FastMCP output.

    Falls back to ``"fastmcp"`` when no prefix can be detected.

    Args:
        tool_name: The raw tool name string (e.g. ``"github_search_repos"``).

    Returns:
        The inferred namespace string.
    """
    if not tool_name:
        return _FALLBACK_NS

    # Dot and slash separators — delegate to MCP adapter logic.
    if "." in tool_name:
        prefix = tool_name.split(".", 1)[0]
        if prefix:
            return prefix
    if "/" in tool_name:
        prefix = tool_name.split("/", 1)[0]
        if prefix:
            return prefix

    # Underscore: FastMCP uses {namespace}_{name} with 2+ segments.
    parts = tool_name.split("_")
    if len(parts) >= 2 and parts[0] and not parts[0].startswith("_"):
        return parts[0]

    return _FALLBACK_NS


def _strip_namespace_prefix(tool_name: str, namespace: str) -> str:
    """Return the short tool name with the namespace prefix removed.

    If the tool name starts with ``{namespace}_``, ``{namespace}.``, or
    ``{namespace}/``, that prefix is stripped. Otherwise the full name is
    returned unchanged.
    """
    for prefix in (f"{namespace}_", f"{namespace}.", f"{namespace}/"):
        if tool_name.startswith(prefix) and len(tool_name) > len(prefix):
            return tool_name[len(prefix) :]
    return tool_name


# ---------------------------------------------------------------------------
# Tool → SelectableItem conversion
# ---------------------------------------------------------------------------


def fastmcp_tool_to_selectable(
    tool_def: dict[str, Any],
    *,
    namespace: str | None = None,
) -> SelectableItem:
    """Convert a FastMCP tool definition dict to a :class:`SelectableItem`.

    Delegates schema/annotation parsing to
    :func:`~contextweaver.adapters.mcp.mcp_tool_to_selectable` and then
    adjusts the ``id``, ``namespace``, ``name``, and ``tags`` to reflect
    FastMCP conventions.

    Expected dict keys (matching the MCP ``tools/list`` wire format):

    - ``name`` (required)
    - ``description`` (required)
    - ``inputSchema`` (optional JSON Schema dict)
    - ``outputSchema`` (optional JSON Schema dict)
    - ``annotations`` (optional MCP annotation hints)
    - ``meta`` (optional dict — FastMCP passes custom metadata here,
      including ``tags`` as a set/list)

    Args:
        tool_def: Raw tool definition dict.
        namespace: Explicit namespace override.  When ``None``, the namespace
            is inferred from the tool name via :func:`infer_fastmcp_namespace`.

    Returns:
        A :class:`SelectableItem` with ``kind="tool"``.

    Raises:
        CatalogError: If required fields (``name``, ``description``) are
            missing.
    """
    # Delegate core conversion to the MCP adapter.
    item = mcp_tool_to_selectable(tool_def)

    full_name: str = item.name
    ns = namespace if namespace is not None else infer_fastmcp_namespace(full_name)
    short_name = _strip_namespace_prefix(full_name, ns)

    # Build tag set: start with "fastmcp", keep annotation-derived tags,
    # merge user-defined tags from meta.
    tags: set[str] = {_FALLBACK_NS}
    for tag in item.tags:
        if tag != "mcp":
            tags.add(tag)

    # FastMCP passes custom metadata through the ``meta`` field.
    meta: dict[str, Any] = tool_def.get("meta") or {}
    meta_tags = meta.get("tags")
    if isinstance(meta_tags, (list, set, tuple)):
        for t in meta_tags:
            if isinstance(t, str) and t:
                tags.add(t)

    # Normalize meta for JSON-serialization safety: coerce set/frozenset → sorted
    # list, tuple → list.  Keeps all keys — only the value types are changed.
    normalized_meta: dict[str, Any] = {
        k: sorted(v) if isinstance(v, (set, frozenset)) else list(v) if isinstance(v, tuple) else v
        for k, v in meta.items()
    }

    logger.debug(
        "fastmcp_tool_to_selectable: name=%s, ns=%s, tags=%s",
        full_name,
        ns,
        sorted(tags),
    )
    return SelectableItem(
        id=f"fastmcp:{full_name}",
        kind=item.kind,
        name=short_name,
        description=item.description,
        tags=sorted(tags),
        namespace=ns,
        args_schema=item.args_schema,
        output_schema=item.output_schema,
        side_effects=item.side_effects,
        cost_hint=item.cost_hint,
        metadata={**item.metadata, **normalized_meta},
    )


# ---------------------------------------------------------------------------
# Batch conversion → Catalog
# ---------------------------------------------------------------------------


def fastmcp_tools_to_catalog(
    tools: list[dict[str, Any]],
    *,
    namespace: str | None = None,
) -> Catalog:
    """Convert a list of FastMCP tool definitions to a populated :class:`Catalog`.

    Args:
        tools: List of raw tool definition dicts.
        namespace: Optional namespace override applied to every item.  When
            ``None``, each tool's namespace is inferred individually.

    Returns:
        A :class:`~contextweaver.routing.catalog.Catalog` with all converted
        items registered.

    Raises:
        CatalogError: If a tool definition is invalid or duplicate IDs are
            encountered.
    """
    catalog = Catalog()
    for tool_def in tools:
        item = fastmcp_tool_to_selectable(tool_def, namespace=namespace)
        catalog.register(item)
    logger.debug("fastmcp_tools_to_catalog: registered %d items", len(tools))
    return catalog


# ---------------------------------------------------------------------------
# Live server discovery (requires ``contextweaver[fastmcp]``)
# ---------------------------------------------------------------------------


async def load_fastmcp_catalog(
    source: object,
    *,
    namespace: str | None = None,
) -> Catalog:
    """Connect to a FastMCP server and return a populated :class:`Catalog`.

    *source* accepts everything that ``fastmcp.Client()`` supports:

    - A ``FastMCP`` server instance (in-memory, zero network)
    - A URL string (``"http://localhost:8000/mcp"``)
    - A file path string (``"my_server.py"``)
    - A config dict (``{"mcpServers": {...}}``)
    - An existing ``fastmcp.Client`` instance

    Requires the ``contextweaver[fastmcp]`` optional extra.

    Args:
        source: FastMCP server source — see above.
        namespace: Optional namespace override applied to every item.

    Returns:
        A :class:`~contextweaver.routing.catalog.Catalog` populated with all
        tools discovered from the server.

    Raises:
        CatalogError: If ``fastmcp`` is not installed or the server cannot
            be reached.
    """
    try:
        from fastmcp import Client
    except ImportError as exc:
        raise CatalogError(
            "FastMCP is not installed. Install with: pip install 'contextweaver[fastmcp]'"
        ) from exc

    client = source if isinstance(source, Client) else Client(source)  # type: ignore[call-overload,unused-ignore]

    try:
        async with client:
            raw_tools = await client.list_tools()
            tool_dicts: list[dict[str, Any]] = []
            for tool in raw_tools:
                # FastMCP 3.x Tool uses camelCase field names natively (inputSchema,
                # outputSchema, meta). DO NOT add by_alias=True — it renames meta → _meta,
                # breaking meta-tag extraction in fastmcp_tool_to_selectable().
                if hasattr(tool, "model_dump"):
                    tool_dicts.append(tool.model_dump(exclude_none=True))
                elif isinstance(tool, dict):
                    tool_dicts.append(tool)
                else:
                    tool_dicts.append(
                        {
                            "name": getattr(tool, "name", ""),
                            "description": getattr(tool, "description", ""),
                            "inputSchema": getattr(tool, "inputSchema", {}),
                            "annotations": getattr(tool, "annotations", None),
                            "outputSchema": getattr(tool, "outputSchema", None),
                            "meta": getattr(tool, "meta", None),
                        }
                    )
    except CatalogError:
        raise
    except Exception as exc:
        raise CatalogError(f"Failed to list tools from FastMCP server: {exc}") from exc

    return fastmcp_tools_to_catalog(tool_dicts, namespace=namespace)


# ---------------------------------------------------------------------------
# CodeMode-style hooks (issue #87)
# ---------------------------------------------------------------------------
#
# These factories produce plain ``Callable`` objects that any runtime
# supporting a custom-discovery-tool hook can call.  They do **not** import
# ``fastmcp`` on either the producer or the consumer side, so the same
# callables work with FastMCP CodeMode, LangChain ``BindToolsMixin`` shims,
# LlamaIndex tool selectors, or a hand-rolled agent loop.  The shape is
# pinned by the issue:
#
#   discovery hook  : ``Callable[[str], list[dict]]``
#   context  hook   : ``Callable[[str, str], str]``
#
# Reference: https://github.com/PrefectHQ/fastmcp/discussions/3365


def make_discovery_tool(
    router: Router,
    catalog: Catalog,
    *,
    top_k: int | None = None,
) -> Callable[[str], list[dict[str, Any]]]:
    """Wrap a :class:`Router` + :class:`Catalog` pair as a discovery callable.

    The returned function accepts a single user query string and returns a
    list of tool dicts (``{"name", "description", "input_schema"}``) for the
    top-ranked candidates.  This is the shape FastMCP CodeMode's custom
    discovery hook expects, but it is intentionally framework-agnostic —
    any runtime that wants a "given this query, hand me a shortlist of
    callable tools" hook can use it.

    Args:
        router: A configured :class:`Router`.  Its own ``top_k`` parameter
            still applies; *top_k* below is an additional ceiling.
        catalog: The :class:`Catalog` whose items the router was built over,
            used to hydrate each candidate id back into name / description /
            input schema.
        top_k: Optional ceiling on the returned shortlist size.  ``None``
            (default) honours whatever the router was configured with.
            When provided, the smaller of ``router.top_k`` and *top_k* wins.

    Returns:
        A pure callable ``(query: str) -> list[dict[str, Any]]`` with no
        captured FastMCP / external-runtime references.  The function is
        side-effect-free aside from the router's own internal scoring cache.

    Raises:
        ConfigError: If *top_k* is negative.
    """
    if top_k is not None and top_k < 0:
        raise ConfigError(f"top_k must be >= 0, got {top_k}")

    def discover(query: str) -> list[dict[str, Any]]:
        result = router.route(query)
        out: list[dict[str, Any]] = []
        for tid in result.candidate_ids:
            if top_k is not None and len(out) >= top_k:
                break
            try:
                hydrated = catalog.hydrate(tid)
            except (ItemNotFoundError, CatalogError):
                # Candidate id not in catalog (graph-only node, e.g. category
                # label).  Skip rather than fail — the discovery hook must
                # never reject a valid query just because one node is
                # virtual.  We continue iterating so a virtual node early in
                # the list does not eat a slot in the ``top_k`` shortlist.
                continue
            # ``args_schema`` is shared with the catalog item at the nested
            # level (Catalog.hydrate makes a shallow copy only).  Deep-copy
            # before handing it to external runtimes so accidental mutation
            # of a nested dict / list cannot corrupt catalog state across
            # subsequent ``discover()`` calls.
            out.append(
                {
                    "name": hydrated.item.name,
                    "description": hydrated.item.description,
                    "input_schema": copy.deepcopy(hydrated.args_schema),
                }
            )
        return out

    return discover


def make_context_hook(
    context_manager: ContextManager,
    *,
    firewall_threshold: int = 2000,
) -> Callable[[str, str], str]:
    """Wrap a :class:`ContextManager` firewall as a (query, raw_result) → summary callable.

    The returned function ingests *raw_result* through the firewall, parking
    the raw bytes in the artifact store and returning the compact summary
    text that should be placed on the LLM prompt.  This is the shape a
    FastMCP CodeMode "context hook" expects, but it is again
    framework-agnostic — any runtime that wants a "give me a budget-aware
    summary of this raw tool output" hook can use it.

    Args:
        context_manager: A configured :class:`ContextManager`.  The hook
            mutates its event log + artifact store on every call.
        firewall_threshold: Character threshold above which the firewall
            kicks in; below it the raw text is returned unchanged.  Matches
            :meth:`ContextManager.ingest_tool_result_sync`'s default.

    Returns:
        A pure callable ``(query, raw_result) -> str``.  *query* is stamped
        onto ``item.metadata["codemode_query"]`` on the firewalled
        :class:`~contextweaver.types.ContextItem` so traces can correlate the
        hook call back to the user turn that triggered it; *raw_result* is
        the verbatim tool output.  No synthetic ``user_turn`` item is
        ingested — the hook is intentionally stateless w.r.t. conversation
        history; only the firewall side-effect (raw bytes parked in the
        artifact store) is intentional.  Returns the firewall summary (or
        the raw result if below threshold).

    Raises:
        ConfigError: If *firewall_threshold* is negative.
    """
    if firewall_threshold < 0:
        raise ConfigError(f"firewall_threshold must be >= 0, got {firewall_threshold}")

    def hook(query: str, raw_result: str) -> str:
        # Stable, collision-resistant ids: short uuid is plenty since the
        # CodeMode hook is a single-shot pipeline, not a long-running session.
        call_id = f"codemode:{uuid.uuid4().hex[:12]}"
        item, _envelope = context_manager.ingest_tool_result_sync(
            tool_call_id=call_id,
            raw_output=raw_result,
            tool_name="codemode.discovery",
            firewall_threshold=firewall_threshold,
        )
        # Record the query as metadata so traces can correlate the hook call
        # back to the user turn that triggered it.  Don't ingest it as a
        # user_turn ContextItem — the hook is stateless w.r.t. conversation
        # history; only the firewall side-effect is intentional.
        item.metadata.setdefault("codemode_query", query)
        return item.text

    return hook

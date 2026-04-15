"""FastMCP adapter for contextweaver.

Bridges FastMCP servers and contextweaver :class:`~contextweaver.routing.catalog.Catalog`
objects.  Converts FastMCP tool definitions into
:class:`~contextweaver.types.SelectableItem` objects and provides live server
discovery via the FastMCP ``Client``.

Core conversion functions (:func:`fastmcp_tool_to_selectable`,
:func:`fastmcp_tools_to_catalog`) work with plain dicts — no ``fastmcp``
install required.  Live server discovery (:func:`load_fastmcp_catalog`)
requires the ``contextweaver[fastmcp]`` optional extra.

FastMCP composition docs: https://gofastmcp.com/servers/composition
"""

from __future__ import annotations

import logging
from typing import Any

from contextweaver.adapters.mcp import mcp_tool_to_selectable
from contextweaver.exceptions import CatalogError
from contextweaver.routing.catalog import Catalog
from contextweaver.types import SelectableItem

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
        from fastmcp import Client  # type: ignore[import-not-found]
    except ImportError as exc:
        raise CatalogError(
            "FastMCP is not installed. Install with: pip install 'contextweaver[fastmcp]'"
        ) from exc

    client: Client
    client = source if isinstance(source, Client) else Client(source)

    try:
        async with client:
            raw_tools = await client.list_tools()
            tool_dicts: list[dict[str, Any]] = []
            for tool in raw_tools:
                # FastMCP list_tools() returns typed Tool objects — convert to dicts.
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

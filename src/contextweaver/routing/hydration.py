"""Public schema-hydration helpers for tool catalogs (issue #261).

Reference architectures and gateway runtimes typically separate the
**routing-shaped** catalog (compact `SelectableItem`s — name, description,
tags, side-effects, cost hint) from the **execution-shaped** catalog
(full JSON Schemas for arguments, examples, constraints). The split keeps
the routing path cheap and the catalog YAML readable; the full schemas
arrive lazily on the gateway's `tool_execute` / `tool_hydrate` boundary.

Before this module existed, every reference architecture that wanted to
show that split had to hand-roll a private ``_FULL_SCHEMAS`` lookup dict
(see the original ``examples/architectures/mcp_context_gateway/main.py``).
This module replaces that pattern with a tiny, public, composable helper
that resolves a tool's input schema from any of the three formats the
ecosystem already produces:

1. an in-memory ``dict[str, dict[str, Any]]`` keyed by tool id;
2. a JSON file shaped ``{"<tool_id>": <input-schema>}`` or
   ``{"tools": [{"name": "...", "inputSchema": {...}}, ...]}``;
3. a sequence of MCP-shaped tool definitions
   (``{"name": "...", "inputSchema": {...}}``) — the wire shape returned
   by ``mcp.client.tools.list_tools()``.

The resolver merges into the existing
:meth:`~contextweaver.routing.catalog.Catalog.hydrate` result so callers
get a single :class:`~contextweaver.envelope.HydrationResult` whether the
schema lived inline on the catalog item or in a sidecar source.

This is library code — no I/O at module load, no third-party imports.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from contextweaver.envelope import HydrationResult
from contextweaver.exceptions import CatalogError, ItemNotFoundError
from contextweaver.routing.catalog import Catalog


class SchemaSource:
    """Resolve a tool's full input schema from a sidecar source.

    A ``SchemaSource`` wraps any of the three accepted shapes (raw dict,
    JSON-file path, MCP tool-def list) and exposes a single
    :meth:`get_schema` method that returns the input schema dict (or
    ``None`` if no schema is registered for the given id).

    Example:
        >>> source = SchemaSource({"bigquery.run_query": {"type": "object"}})
        >>> source.get_schema("bigquery.run_query")
        {'type': 'object'}
        >>> source.get_schema("missing.tool") is None
        True

    Schema sources are read-only after construction. Mutating the input
    mapping does not propagate; the constructor copies entries eagerly so
    later mutations to the caller's dict cannot bypass the snapshot.
    """

    __slots__ = ("_schemas",)

    def __init__(self, schemas: Mapping[str, dict[str, Any]] | None = None) -> None:
        """Initialise an empty source or seed it from an existing mapping.

        Args:
            schemas: Optional mapping of tool id → input-schema dict.
                Each value is shallow-copied to insulate the source from
                later caller mutations.
        """
        self._schemas: dict[str, dict[str, Any]] = (
            {k: dict(v) for k, v in schemas.items()} if schemas else {}
        )

    @classmethod
    def from_json_file(cls, path: str | Path) -> SchemaSource:
        """Build a :class:`SchemaSource` from a JSON file.

        Two file shapes are accepted:

        - **Flat mapping** ``{"tool.id": {<schema>}, ...}``
        - **MCP-style array** ``{"tools": [{"name": "...", "inputSchema": ...}, ...]}``

        Args:
            path: Filesystem path to a JSON file.

        Returns:
            A :class:`SchemaSource` seeded from the file.

        Raises:
            CatalogError: If the file cannot be read or parsed, or if the
                top-level shape is neither a mapping nor a ``{"tools": []}``
                envelope.
        """
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise CatalogError(f"Cannot read schema-source file: {exc}") from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CatalogError(f"Invalid JSON in schema-source file {path!s}: {exc}") from exc
        if isinstance(data, dict) and "tools" in data and isinstance(data["tools"], list):
            return cls.from_mcp_tools(data["tools"])
        if isinstance(data, dict):
            return cls(data)
        raise CatalogError(
            f"Schema-source file {path!s} must be a JSON object (got {type(data).__name__})"
        )

    @classmethod
    def from_mcp_tools(cls, tool_defs: Iterable[Mapping[str, Any]]) -> SchemaSource:
        """Build a :class:`SchemaSource` from MCP-shaped tool defs.

        Each tool def must carry a ``"name"`` (used as the lookup key) and
        may carry an ``"inputSchema"`` dict. Defs without an
        ``inputSchema`` are silently skipped — that mirrors the upstream
        MCP wire contract where ``inputSchema`` is optional.

        Args:
            tool_defs: Iterable of MCP-shaped tool-def dicts (the shape
                returned by ``mcp.client.session.ClientSession.list_tools()``
                ``.tools[i].model_dump()``, also the shape carried on
                ``contextweaver.adapters.mcp.mcp_tool_to_selectable``'s
                input).

        Returns:
            A :class:`SchemaSource` seeded from the iterable.

        Raises:
            CatalogError: If any def is not a mapping or is missing a
                ``"name"`` key.
        """
        schemas: dict[str, dict[str, Any]] = {}
        for idx, raw in enumerate(tool_defs):
            if not isinstance(raw, Mapping):
                raise CatalogError(f"Tool def #{idx} must be a mapping (got {type(raw).__name__})")
            name = raw.get("name")
            if not isinstance(name, str) or not name:
                raise CatalogError(f"Tool def #{idx} missing non-empty 'name'")
            schema = raw.get("inputSchema")
            if isinstance(schema, Mapping):
                schemas[name] = dict(schema)
        return cls(schemas)

    def get_schema(self, tool_id: str) -> dict[str, Any] | None:
        """Return the input schema for *tool_id*, or ``None`` if absent.

        Args:
            tool_id: The tool id to look up.

        Returns:
            A shallow copy of the registered schema dict, or ``None`` if
            no schema is registered. Callers may mutate the returned dict
            without affecting the source.
        """
        schema = self._schemas.get(tool_id)
        return dict(schema) if schema is not None else None

    def known_ids(self) -> list[str]:
        """Return all tool ids registered in this source, sorted.

        Returns:
            A new list of registered tool ids in ascending order.
        """
        return sorted(self._schemas)


def hydrate_with_schema(
    catalog: Catalog,
    tool_id: str,
    source: SchemaSource | Mapping[str, dict[str, Any]] | None = None,
) -> HydrationResult:
    """Hydrate *tool_id* from *catalog*, merging a sidecar input schema if present.

    The catalog's ``hydrate(tool_id)`` is the authoritative source of the
    item and any examples / constraints already attached on the
    ``SelectableItem``. If the item's ``args_schema`` is empty *and*
    *source* carries a schema for the same id, this function injects that
    schema into the returned :class:`HydrationResult`. Otherwise the
    catalog's own ``args_schema`` is preserved verbatim — the sidecar
    never overrides an explicitly populated inline schema.

    Args:
        catalog: The :class:`~contextweaver.routing.catalog.Catalog` to
            hydrate from.
        tool_id: Unique identifier of the tool to hydrate.
        source: Optional sidecar schema source. Accepts either a
            :class:`SchemaSource` or a plain ``Mapping[str, dict]`` for
            convenience (it is wrapped on the fly).

    Returns:
        A :class:`HydrationResult` with the catalog's metadata plus the
        merged input schema. The result's ``args_schema`` is always a
        fresh dict; mutating it does not affect the catalog or the source.

    Raises:
        ItemNotFoundError: If *tool_id* is not registered in *catalog*.
    """
    result = catalog.hydrate(tool_id)
    if result.args_schema:
        return result
    if source is None:
        return result
    resolved_source = source if isinstance(source, SchemaSource) else SchemaSource(source)
    sidecar = resolved_source.get_schema(tool_id)
    if sidecar is None:
        return result
    # Build a new HydrationResult so we don't mutate Catalog.hydrate()'s
    # shallow-copy semantics for the in-catalog dict.
    return HydrationResult(
        item=result.item,
        args_schema=sidecar,
        examples=list(result.examples),
        constraints=dict(result.constraints),
    )


def lazy_schema_resolver(
    catalog: Catalog,
    source: SchemaSource | Mapping[str, dict[str, Any]] | None = None,
) -> _LazyResolver:
    """Return a callable that hydrates tool ids on demand.

    Convenience wrapper for the common pattern in reference architectures:
    callers want a ``schema_for(tool_id) -> dict | None`` shape rather
    than the full :class:`HydrationResult`. The returned object is
    callable and also exposes a ``hydrate(tool_id)`` method that returns
    the full :class:`HydrationResult` for callers who want both.

    Args:
        catalog: The catalog to resolve against.
        source: Optional sidecar schema source.

    Returns:
        A :class:`_LazyResolver` instance bound to *catalog* and *source*.

    Example:
        >>> resolver = lazy_schema_resolver(catalog, source)
        >>> schema = resolver("bigquery.run_query")
        >>> hydrated = resolver.hydrate("bigquery.run_query")
    """
    return _LazyResolver(catalog, source)


class _LazyResolver:
    """Callable wrapper produced by :func:`lazy_schema_resolver`."""

    __slots__ = ("_catalog", "_source")

    def __init__(
        self,
        catalog: Catalog,
        source: SchemaSource | Mapping[str, dict[str, Any]] | None,
    ) -> None:
        self._catalog = catalog
        self._source: SchemaSource | None
        if source is None:
            self._source = None
        elif isinstance(source, SchemaSource):
            self._source = source
        else:
            self._source = SchemaSource(source)

    def __call__(self, tool_id: str) -> dict[str, Any] | None:
        """Return the input schema for *tool_id*, or ``None`` if absent.

        Args:
            tool_id: The tool id to resolve.

        Returns:
            The merged input schema or ``None`` if neither the catalog
            nor the sidecar source carries one. ``None`` is also returned
            when the tool id is not registered (rather than raising) so
            this lookup is safe in template/rendering contexts.
        """
        try:
            result = hydrate_with_schema(self._catalog, tool_id, self._source)
        except ItemNotFoundError:
            return None
        return result.args_schema if result.args_schema else None

    def hydrate(self, tool_id: str) -> HydrationResult:
        """Full :class:`HydrationResult` for *tool_id*.

        Args:
            tool_id: The tool id to hydrate.

        Returns:
            A :class:`HydrationResult` with the merged schema.

        Raises:
            ItemNotFoundError: If *tool_id* is not registered in the catalog.
        """
        return hydrate_with_schema(self._catalog, tool_id, self._source)

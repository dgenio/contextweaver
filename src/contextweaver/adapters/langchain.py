"""LangChain adapter for contextweaver (issue #502).

Bridges [LangChain](https://python.langchain.com/) tools and
:class:`~contextweaver.routing.catalog.Catalog` objects.  Converts LangChain
``BaseTool`` instances (or the equivalent plain-dict shape) into
:class:`~contextweaver.types.SelectableItem` objects so agents built on plain
LangChain — or the LangGraph reference architecture — can route through
contextweaver's bounded-choice router instead of dumping every tool definition
into the prompt.

This closes the gap recorded in issue #401 for LangChain specifically: the
project shipped a ``[langchain]`` extra, a docs guide, and a LangGraph example
but no importable ``BaseTool → SelectableItem`` adapter.

The plain-dict conversion functions (:func:`langchain_tool_to_selectable`,
:func:`langchain_tools_to_catalog`) work without ``langchain-core`` installed —
they accept dicts shaped like a serialised ``BaseTool``.  Live conversion of
real ``langchain_core.tools.BaseTool`` instances
(:func:`load_langchain_catalog`) requires the ``contextweaver[langchain]``
optional extra; the helper itself does not import the library — it duck-types
the inputs so the conversion path is testable without the extra.

LangChain tools docs: https://python.langchain.com/docs/concepts/tools/
"""

from __future__ import annotations

import logging
from typing import Any

from contextweaver.adapters._framework_common import (
    coerce_schema_dict,
    collect_tags,
    infer_namespace,
    require_name_description,
    strip_namespace_prefix,
)
from contextweaver.exceptions import CatalogError
from contextweaver.routing.catalog import Catalog
from contextweaver.types import SelectableItem

logger = logging.getLogger("contextweaver.adapters")

_FALLBACK_NS = "langchain"
_ID_PREFIX = "langchain"


def infer_langchain_namespace(tool_name: str) -> str:
    """Infer a namespace from a LangChain tool name.

    LangChain tools are commonly named in snake_case (e.g. ``tavily_search``,
    ``sql_db_query``); the namespace is the first dot- / slash- / underscore-
    separated segment.  Falls back to ``"langchain"`` when no prefix can be
    detected.

    Args:
        tool_name: The raw tool name string.

    Returns:
        The inferred namespace string.
    """
    return infer_namespace(tool_name, fallback=_FALLBACK_NS)


def _args_schema_value(tool_def: dict[str, Any]) -> object:
    """Pick the schema source from a LangChain tool dict.

    ``args_schema`` (a Pydantic model class or a JSON-Schema dict) wins; some
    serialised tools expose only ``args`` (the properties mapping), which is
    wrapped into a minimal object schema so the result is a valid JSON Schema.
    """
    schema = tool_def.get("args_schema")
    if schema is not None:
        return schema
    args = tool_def.get("args")
    if isinstance(args, dict) and args:
        return {"type": "object", "properties": dict(args)}
    return None


def langchain_tool_to_selectable(
    tool_def: dict[str, Any],
    *,
    namespace: str | None = None,
) -> SelectableItem:
    """Convert a LangChain tool definition dict to a :class:`SelectableItem`.

    The dict shape mirrors the field set on ``langchain_core.tools.BaseTool``:

    - ``name`` (required): the tool's display name.
    - ``description`` (required): natural-language description for the LLM.
    - ``args_schema`` (optional): a JSON-Schema dict or a Pydantic
      ``BaseModel`` class (converted via ``model_json_schema()``).
    - ``args`` (optional): the bare ``properties`` mapping LangChain exposes
      via ``BaseTool.args``; used only when ``args_schema`` is absent.
    - ``tags`` (optional): list of tag strings.
    - ``return_direct`` (optional): LangChain flag indicating the tool's raw
      output should be returned directly; surfaced under ``metadata``.
    - ``metadata`` (optional): the tool's own ``metadata`` dict, preserved
      under ``metadata["langchain_metadata"]``.

    Args:
        tool_def: Raw tool definition dict.
        namespace: Explicit namespace override.  When ``None``, the namespace
            is inferred from the tool name via :func:`infer_langchain_namespace`.

    Returns:
        A :class:`SelectableItem` with ``kind="tool"`` and an ``id`` of
        ``"langchain:{name}"``.

    Raises:
        CatalogError: If required fields (``name``, ``description``) are
            missing or non-string.
    """
    raw_name, raw_description = require_name_description(tool_def, label="LangChain")

    ns = namespace if namespace is not None else infer_langchain_namespace(raw_name)
    short_name = strip_namespace_prefix(raw_name, ns)

    tags = collect_tags(tool_def.get("tags"), fallback=_FALLBACK_NS)
    args_schema = coerce_schema_dict(_args_schema_value(tool_def))

    metadata: dict[str, Any] = {}
    if "return_direct" in tool_def and tool_def["return_direct"] is not None:
        metadata["return_direct"] = bool(tool_def["return_direct"])
    raw_metadata = tool_def.get("metadata")
    if isinstance(raw_metadata, dict) and raw_metadata:
        metadata["langchain_metadata"] = dict(raw_metadata)

    logger.debug(
        "langchain_tool_to_selectable: name=%s, ns=%s, tags=%s",
        raw_name,
        ns,
        tags,
    )
    return SelectableItem(
        id=f"{_ID_PREFIX}:{raw_name}",
        kind="tool",
        name=short_name,
        description=raw_description,
        tags=tags,
        namespace=ns,
        args_schema=args_schema,
        metadata=metadata,
    )


# Alias matching the issue #502 spelling.
selectable_from_langchain_tool = langchain_tool_to_selectable


def langchain_tools_to_catalog(
    tools: list[dict[str, Any]],
    *,
    namespace: str | None = None,
) -> Catalog:
    """Convert a list of LangChain tool definitions to a populated :class:`Catalog`.

    Args:
        tools: List of raw tool definition dicts.
        namespace: Optional namespace override applied to every item.  When
            ``None``, each tool's namespace is inferred individually.

    Returns:
        A populated :class:`~contextweaver.routing.catalog.Catalog`.

    Raises:
        CatalogError: If a tool definition is invalid or duplicate IDs are
            encountered.
    """
    catalog = Catalog()
    for tool_def in tools:
        catalog.register(langchain_tool_to_selectable(tool_def, namespace=namespace))
    logger.debug("langchain_tools_to_catalog: registered %d items", len(tools))
    return catalog


def load_langchain_catalog(
    tools: list[object],
    *,
    namespace: str | None = None,
) -> Catalog:
    """Convert a list of live ``langchain_core.tools.BaseTool`` instances to a :class:`Catalog`.

    Reads ``name`` / ``description`` / ``args_schema`` / ``tags`` /
    ``return_direct`` / ``metadata`` off each instance and routes them through
    :func:`langchain_tools_to_catalog`.  The framework dep is **not** imported
    by this module — the helper duck-types the inputs so callers can test the
    conversion path without the ``contextweaver[langchain]`` extra installed.

    Args:
        tools: List of live LangChain tool instances (or any object exposing
            ``name`` / ``description`` attributes).
        namespace: Optional namespace override applied to every item.

    Returns:
        A populated :class:`Catalog`.

    Raises:
        CatalogError: If a tool object is missing required attributes.
    """
    tool_dicts: list[dict[str, Any]] = []
    for tool in tools:
        name = getattr(tool, "name", None)
        description = getattr(tool, "description", None)
        if not isinstance(name, str) or not name:
            raise CatalogError(f"LangChain tool {tool!r} is missing a non-empty 'name' attribute.")
        if not isinstance(description, str) or not description:
            raise CatalogError(
                f"LangChain tool {name!r} is missing a non-empty 'description' attribute."
            )
        tool_dicts.append(
            {
                "name": name,
                "description": description,
                "args_schema": getattr(tool, "args_schema", None),
                "args": getattr(tool, "args", None),
                "tags": list(getattr(tool, "tags", None) or []),
                "return_direct": getattr(tool, "return_direct", None),
                "metadata": getattr(tool, "metadata", None),
            }
        )
    return langchain_tools_to_catalog(tool_dicts, namespace=namespace)


__all__ = [
    "infer_langchain_namespace",
    "langchain_tool_to_selectable",
    "langchain_tools_to_catalog",
    "load_langchain_catalog",
    "selectable_from_langchain_tool",
]

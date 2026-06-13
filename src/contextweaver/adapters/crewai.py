"""CrewAI adapter for contextweaver (issue #193).

Bridges [CrewAI](https://docs.crewai.com/) tools and
:class:`~contextweaver.routing.catalog.Catalog` objects.  Converts CrewAI
``BaseTool`` instances (or the equivalent plain-dict shape) into
:class:`~contextweaver.types.SelectableItem` objects so that crews built with
``crewai.Agent`` and ``crewai.Crew`` can route through contextweaver's
bounded-choice router instead of dumping every tool definition into the
prompt.

The plain-dict conversion functions (:func:`crewai_tool_to_selectable`,
:func:`crewai_tools_to_catalog`) work without the ``crewai`` package
installed — they accept dicts with the same shape that
``BaseTool.model_dump()`` emits.  Live conversion of real
``crewai.tools.BaseTool`` instances (:func:`load_crewai_catalog`) requires
the ``contextweaver[crewai]`` optional extra.

CrewAI tools docs: https://docs.crewai.com/concepts/tools
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

_FALLBACK_NS = "crewai"


def infer_crewai_namespace(tool_name: str) -> str:
    """Infer a namespace from a CrewAI tool name.

    CrewAI tools are usually named in human-readable PascalCase or
    snake_case (e.g. ``SerperDevTool``, ``code_interpreter``).  This helper
    extracts a namespace using the same dot- / slash- / underscore-
    separated prefix rules that the other framework adapters use, falling
    back to ``"crewai"`` when no prefix can be detected.

    Args:
        tool_name: The raw tool name string.

    Returns:
        The inferred namespace string.
    """
    return infer_namespace(tool_name, fallback=_FALLBACK_NS)


def crewai_tool_to_selectable(
    tool_def: dict[str, Any],
    *,
    namespace: str | None = None,
) -> SelectableItem:
    """Convert a CrewAI tool definition dict to a :class:`SelectableItem`.

    The dict shape mirrors the field set on ``crewai.tools.BaseTool``
    (which is a Pydantic ``BaseModel``):

    - ``name`` (required): the tool's display name.
    - ``description`` (required): natural-language description used by the
      LLM at routing time.
    - ``args_schema`` (optional): either a JSON-schema dict or a Pydantic
      model class — both are accepted; the class is converted via
      ``model_json_schema()``.
    - ``tags`` (optional): list of tag strings.
    - ``result_as_answer`` (optional): CrewAI flag indicating the tool's
      raw return should bypass agent reflection; surfaced as a metadata
      field for downstream consumers and is **not** the same as
      ``side_effects``.

    Args:
        tool_def: Raw tool definition dict (typically the result of
            ``crewai.tools.BaseTool().model_dump()`` on a live instance).
        namespace: Explicit namespace override.  When ``None``, the
            namespace is inferred from the tool name via
            :func:`infer_crewai_namespace`.

    Returns:
        A :class:`SelectableItem` with ``kind="tool"`` and ``id`` of the
        form ``"crewai:{name}"``.

    Raises:
        CatalogError: If required fields (``name``, ``description``) are
            missing or non-string.
    """
    raw_name, raw_description = require_name_description(tool_def, label="CrewAI")

    full_name = raw_name
    ns = namespace if namespace is not None else infer_crewai_namespace(full_name)
    short_name = strip_namespace_prefix(full_name, ns)

    tags = collect_tags(tool_def.get("tags"), fallback=_FALLBACK_NS)

    args_schema = coerce_schema_dict(tool_def.get("args_schema"))

    metadata: dict[str, Any] = {}
    if "result_as_answer" in tool_def:
        metadata["result_as_answer"] = bool(tool_def["result_as_answer"])
    if "max_usage_count" in tool_def and tool_def["max_usage_count"] is not None:
        metadata["max_usage_count"] = tool_def["max_usage_count"]

    # Store the preamble-free description when CrewAI's enriched format is
    # detected (pattern: "Tool Name: ...\nTool Description: <original>").
    _preamble_marker = "\nTool Description: "
    if _preamble_marker in raw_description:
        metadata["original_description"] = raw_description.split(_preamble_marker, 1)[1]

    logger.debug(
        "crewai_tool_to_selectable: name=%s, ns=%s, tags=%s",
        full_name,
        ns,
        sorted(tags),
    )
    return SelectableItem(
        id=f"crewai:{full_name}",
        kind="tool",
        name=short_name,
        description=raw_description,
        tags=sorted(tags),
        namespace=ns,
        args_schema=args_schema,
        metadata=metadata,
    )


def crewai_tools_to_catalog(
    tools: list[dict[str, Any]],
    *,
    namespace: str | None = None,
) -> Catalog:
    """Convert a list of CrewAI tool definitions to a populated :class:`Catalog`.

    Args:
        tools: List of raw tool definition dicts.
        namespace: Optional namespace override applied to every item.  When
            ``None``, each tool's namespace is inferred individually.

    Returns:
        A :class:`~contextweaver.routing.catalog.Catalog` with all
        converted items registered.

    Raises:
        CatalogError: If a tool definition is invalid or duplicate IDs are
            encountered.
    """
    catalog = Catalog()
    for tool_def in tools:
        item = crewai_tool_to_selectable(tool_def, namespace=namespace)
        catalog.register(item)
    logger.debug("crewai_tools_to_catalog: registered %d items", len(tools))
    return catalog


def load_crewai_catalog(
    tools: list[object],
    *,
    namespace: str | None = None,
) -> Catalog:
    """Convert a list of live ``crewai.tools.BaseTool`` instances to a :class:`Catalog`.

    Each tool is dumped via Pydantic's ``model_dump()`` and routed through
    :func:`crewai_tools_to_catalog`.  Non-CrewAI objects are accepted as
    long as they expose ``name`` and ``description`` attributes — the
    function reads them via ``getattr`` and constructs the equivalent
    dict.

    This is a synchronous helper because CrewAI tools are sync objects;
    the FastMCP adapter's async counterpart exists because FastMCP tools
    are discovered over the wire.

    Requires the ``contextweaver[crewai]`` optional extra **only** if
    you intend to import ``crewai`` itself in the same process; the
    helper itself does not import the library.

    Args:
        tools: List of live CrewAI tool instances (or any object exposing
            ``name`` / ``description`` attributes plus an optional
            ``args_schema`` / ``tags`` / ``model_dump``).
        namespace: Optional namespace override applied to every item.

    Returns:
        A populated :class:`Catalog`.

    Raises:
        CatalogError: If a tool object is missing required attributes.
    """
    tool_dicts: list[dict[str, Any]] = []
    for tool in tools:
        if hasattr(tool, "model_dump"):
            try:
                dumped = tool.model_dump()
            except Exception as exc:
                raise CatalogError(f"Failed to dump CrewAI tool {tool!r}: {exc}") from exc
            if not isinstance(dumped, dict):
                raise CatalogError(f"CrewAI tool {tool!r}.model_dump() did not return a dict.")
            # Replace args_schema with the raw class so _args_schema_dict
            # can convert it via model_json_schema().  model_dump() drops
            # class objects by default and emits ``None`` instead.
            raw_args = getattr(tool, "args_schema", None)
            if raw_args is not None:
                dumped["args_schema"] = raw_args
            tool_dicts.append(dumped)
        else:
            name = getattr(tool, "name", None)
            description = getattr(tool, "description", None)
            if not isinstance(name, str) or not name:
                raise CatalogError(f"CrewAI tool {tool!r} is missing a non-empty 'name' attribute.")
            if not isinstance(description, str) or not description:
                raise CatalogError(
                    f"CrewAI tool {name!r} is missing a non-empty 'description' attribute."
                )
            tool_dicts.append(
                {
                    "name": name,
                    "description": description,
                    "args_schema": getattr(tool, "args_schema", None),
                    "tags": list(getattr(tool, "tags", []) or []),
                }
            )
    return crewai_tools_to_catalog(tool_dicts, namespace=namespace)

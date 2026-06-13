"""OpenAI Agents SDK adapter for contextweaver (issue #501).

Bridges the [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)
function tools and run items to contextweaver-native types, following the same
pure-converter pattern as the CrewAI / Pydantic AI / smolagents / Agno adapters.

Two surfaces:

1. **Tool catalog** — :func:`openai_agents_tool_to_selectable`,
   :func:`openai_agents_tools_to_catalog`, :func:`load_openai_agents_catalog`
   convert ``FunctionTool`` definitions (or the equivalent plain-dict shape)
   into :class:`~contextweaver.types.SelectableItem` objects so an Agents SDK
   app can route through contextweaver's bounded-choice router instead of
   dumping every tool definition into the prompt.

2. **Run ingestion** — :func:`from_openai_agents_run` maps a run's items
   (``RunResult.new_items`` / session history) to
   :class:`~contextweaver.types.ContextItem`s with ``parent_id`` links so
   dependency closure includes a tool call when its result is selected.

The plain-dict / item-dict paths work without the ``openai-agents`` package
installed; the live helpers accept real SDK objects when the
``contextweaver[openai-agents]`` optional extra is installed.  This module
imports no SDK at load time — it duck-types its inputs.

Agents SDK tools docs: https://openai.github.io/openai-agents-python/tools/
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from contextweaver.adapters._framework_common import (
    coerce_schema_dict,
    collect_tags,
    infer_namespace,
    require_name_description,
    strip_namespace_prefix,
)
from contextweaver.adapters._openai_agents_run import decode_run_items
from contextweaver.exceptions import CatalogError
from contextweaver.routing.catalog import Catalog
from contextweaver.types import SelectableItem

if TYPE_CHECKING:
    from contextweaver.context.manager import ContextManager
    from contextweaver.types import ContextItem

logger = logging.getLogger("contextweaver.adapters")

_FALLBACK_NS = "openai_agents"
_ID_PREFIX = "openai_agents"


def infer_openai_agents_namespace(tool_name: str) -> str:
    """Infer a namespace from an OpenAI Agents SDK tool name.

    Falls back to ``"openai_agents"`` when no dot- / slash- / underscore-
    separated prefix can be detected.

    Args:
        tool_name: The raw tool name string.

    Returns:
        The inferred namespace string.
    """
    return infer_namespace(tool_name, fallback=_FALLBACK_NS)


def openai_agents_tool_to_selectable(
    tool_def: dict[str, Any],
    *,
    namespace: str | None = None,
) -> SelectableItem:
    """Convert an Agents SDK function-tool definition dict to a :class:`SelectableItem`.

    The dict shape mirrors the field set on ``agents.FunctionTool``:

    - ``name`` (required): the tool's display name.
    - ``description`` (required): natural-language description for the LLM.
    - ``params_json_schema`` (optional): the tool's JSON Schema, or a Pydantic
      ``BaseModel`` class (converted via ``model_json_schema()``).
    - ``args_schema`` (optional alias): accepted for symmetry with the other
      adapters — when both are present, ``params_json_schema`` wins.
    - ``tags`` (optional): list of tag strings.
    - ``strict`` / ``strict_json_schema`` (optional): the SDK's strict-mode
      flag, surfaced under ``metadata``.

    Args:
        tool_def: Raw tool definition dict.
        namespace: Explicit namespace override.  When ``None``, the namespace
            is inferred from the tool name via
            :func:`infer_openai_agents_namespace`.

    Returns:
        A :class:`SelectableItem` with ``kind="tool"`` and an ``id`` of
        ``"openai_agents:{name}"``.

    Raises:
        CatalogError: If required fields (``name``, ``description``) are
            missing or non-string.
    """
    raw_name, raw_description = require_name_description(tool_def, label="OpenAI Agents")

    ns = namespace if namespace is not None else infer_openai_agents_namespace(raw_name)
    short_name = strip_namespace_prefix(raw_name, ns)

    tags = collect_tags(tool_def.get("tags"), fallback=_FALLBACK_NS)
    schema_value = tool_def.get("params_json_schema", tool_def.get("args_schema"))
    args_schema = coerce_schema_dict(schema_value)

    metadata: dict[str, Any] = {}
    strict = tool_def.get("strict", tool_def.get("strict_json_schema"))
    if strict is not None:
        metadata["strict"] = bool(strict)

    logger.debug(
        "openai_agents_tool_to_selectable: name=%s, ns=%s, tags=%s",
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


# Alias matching the issue #501 spelling.
selectable_from_openai_agents_tool = openai_agents_tool_to_selectable


def openai_agents_tools_to_catalog(
    tools: list[dict[str, Any]],
    *,
    namespace: str | None = None,
) -> Catalog:
    """Convert a list of Agents SDK tool definitions to a populated :class:`Catalog`.

    Args:
        tools: List of raw tool definition dicts.
        namespace: Optional namespace override applied to every item.

    Returns:
        A populated :class:`~contextweaver.routing.catalog.Catalog`.

    Raises:
        CatalogError: If a tool definition is invalid or duplicate IDs are
            encountered.
    """
    catalog = Catalog()
    for tool_def in tools:
        catalog.register(openai_agents_tool_to_selectable(tool_def, namespace=namespace))
    logger.debug("openai_agents_tools_to_catalog: registered %d items", len(tools))
    return catalog


def load_openai_agents_catalog(
    tools: list[object],
    *,
    namespace: str | None = None,
) -> Catalog:
    """Convert a list of live Agents SDK ``FunctionTool`` instances to a :class:`Catalog`.

    Reads ``name`` / ``description`` / ``params_json_schema`` /
    ``strict_json_schema`` off each instance.  The framework dep is **not**
    imported by this module — the helper duck-types the inputs so callers can
    test the conversion path without the ``contextweaver[openai-agents]`` extra
    installed.

    Args:
        tools: List of live ``FunctionTool`` instances (or any object exposing
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
            raise CatalogError(
                f"OpenAI Agents tool {tool!r} is missing a non-empty 'name' attribute."
            )
        if not isinstance(description, str) or not description:
            raise CatalogError(
                f"OpenAI Agents tool {name!r} is missing a non-empty 'description' attribute."
            )
        tool_dicts.append(
            {
                "name": name,
                "description": description,
                "params_json_schema": getattr(tool, "params_json_schema", None),
                "strict_json_schema": getattr(tool, "strict_json_schema", None),
                "tags": list(getattr(tool, "tags", None) or []),
            }
        )
    return openai_agents_tools_to_catalog(tool_dicts, namespace=namespace)


def from_openai_agents_run(
    run_or_items: object,
    into: ContextManager | None = None,
) -> list[ContextItem]:
    """Convert an Agents SDK run's items into :class:`ContextItem`s.

    Accepts a run/result object exposing ``new_items`` / ``items`` or a plain
    list of run-item dicts/objects.  Mapping rules:

    - message output → :data:`ItemKind.agent_msg` (text falls back to a
      deterministic JSON dump of the item when it carries no readable text).
    - tool call → :data:`ItemKind.tool_call` (text is the JSON-encoded args);
      ``tool_call_id`` is preserved in metadata.
    - tool-call output → :data:`ItemKind.tool_result` with ``parent_id`` set to
      the originating tool call so dependency closure links the pair.
    - handoff → :data:`ItemKind.agent_msg` with ``metadata["handoff"]=True``.
    - reasoning → :data:`ItemKind.agent_msg` (skipped when it carries no text).
    - approval / MCP / compaction control items → skipped (no conversational
      content). Genuinely unknown item types still raise.

    Args:
        run_or_items: A run/result object or a list of run items.
        into: Optional :class:`~contextweaver.context.manager.ContextManager`
            to append each item to.

    Returns:
        A list of :class:`ContextItem` in run order.

    Raises:
        CatalogError: On unknown item types or malformed payloads.
    """
    return decode_run_items(run_or_items, into)


__all__ = [
    "from_openai_agents_run",
    "infer_openai_agents_namespace",
    "load_openai_agents_catalog",
    "openai_agents_tool_to_selectable",
    "openai_agents_tools_to_catalog",
    "selectable_from_openai_agents_tool",
]

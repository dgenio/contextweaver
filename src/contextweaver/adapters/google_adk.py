"""Google ADK adapter for contextweaver (issue #547).

Bridges [Google Agent Development Kit (ADK)](https://google.github.io/adk-docs/)
tools and session events to contextweaver-native types, following the same
pure-converter pattern as the CrewAI / Pydantic AI / smolagents / Agno adapters.

Two surfaces:

1. **Tool catalog** — :func:`google_adk_tool_to_selectable`,
   :func:`google_adk_tools_to_catalog`, :func:`load_google_adk_catalog`
   convert ADK ``FunctionTool`` definitions (or the equivalent plain-dict
   shape) into :class:`~contextweaver.types.SelectableItem` objects so an ADK
   agent can route through contextweaver's bounded-choice router instead of
   dumping every tool definition into the prompt.

2. **Session ingestion** — :func:`from_google_adk_session` maps a
   ``Session.events`` chain to :class:`~contextweaver.types.ContextItem`s with
   ``parent_id`` links so dependency closure includes a ``function_call`` when
   its ``function_response`` is selected.

The plain-dict / event-dict paths work without the ``google-adk`` package
installed; the live helpers accept real ADK objects when the
``contextweaver[google-adk]`` optional extra is installed.  This module imports
no SDK at load time — it duck-types its inputs.

ADK tools docs: https://google.github.io/adk-docs/tools/
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
from contextweaver.adapters._google_adk_session import decode_session
from contextweaver.exceptions import CatalogError
from contextweaver.routing.catalog import Catalog
from contextweaver.types import SelectableItem

if TYPE_CHECKING:
    from contextweaver.context.manager import ContextManager
    from contextweaver.types import ContextItem

logger = logging.getLogger("contextweaver.adapters")

_FALLBACK_NS = "google_adk"
_ID_PREFIX = "google_adk"


def infer_google_adk_namespace(tool_name: str) -> str:
    """Infer a namespace from a Google ADK tool name.

    Falls back to ``"google_adk"`` when no dot- / slash- / underscore-
    separated prefix can be detected.

    Args:
        tool_name: The raw tool name string.

    Returns:
        The inferred namespace string.
    """
    return infer_namespace(tool_name, fallback=_FALLBACK_NS)


def _parameters_value(tool_def: dict[str, Any]) -> object:
    """Pick the schema source from an ADK tool dict.

    ``parameters`` (the ``FunctionDeclaration.parameters`` JSON Schema or a
    Pydantic model class) wins; ``args_schema`` is accepted as an alias.
    """
    params = tool_def.get("parameters")
    if params is not None:
        return params
    return tool_def.get("args_schema")


def google_adk_tool_to_selectable(
    tool_def: dict[str, Any],
    *,
    namespace: str | None = None,
) -> SelectableItem:
    """Convert a Google ADK tool definition dict to a :class:`SelectableItem`.

    The dict shape mirrors an ADK ``FunctionDeclaration``:

    - ``name`` (required): the tool's display name.
    - ``description`` (required): natural-language description for the LLM.
    - ``parameters`` (optional): the JSON Schema for the tool's args, or a
      Pydantic ``BaseModel`` class (converted via ``model_json_schema()``).
    - ``args_schema`` (optional alias): used only when ``parameters`` is absent.
    - ``tags`` (optional): list of tag strings.
    - ``is_long_running`` (optional): the ADK long-running flag, surfaced
      under ``metadata``.

    Args:
        tool_def: Raw tool definition dict.
        namespace: Explicit namespace override.  When ``None``, the namespace
            is inferred from the tool name via
            :func:`infer_google_adk_namespace`.

    Returns:
        A :class:`SelectableItem` with ``kind="tool"`` and an ``id`` of
        ``"google_adk:{name}"``.

    Raises:
        CatalogError: If required fields (``name``, ``description``) are
            missing or non-string.
    """
    raw_name, raw_description = require_name_description(tool_def, label="Google ADK")

    ns = namespace if namespace is not None else infer_google_adk_namespace(raw_name)
    short_name = strip_namespace_prefix(raw_name, ns)

    tags = collect_tags(tool_def.get("tags"), fallback=_FALLBACK_NS)
    args_schema = coerce_schema_dict(_parameters_value(tool_def))

    metadata: dict[str, Any] = {}
    if "is_long_running" in tool_def and tool_def["is_long_running"] is not None:
        metadata["is_long_running"] = bool(tool_def["is_long_running"])

    logger.debug(
        "google_adk_tool_to_selectable: name=%s, ns=%s, tags=%s",
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


# Alias matching the issue #547 spelling.
selectable_from_google_adk_tool = google_adk_tool_to_selectable


def google_adk_tools_to_catalog(
    tools: list[dict[str, Any]],
    *,
    namespace: str | None = None,
) -> Catalog:
    """Convert a list of Google ADK tool definitions to a populated :class:`Catalog`.

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
        catalog.register(google_adk_tool_to_selectable(tool_def, namespace=namespace))
    logger.debug("google_adk_tools_to_catalog: registered %d items", len(tools))
    return catalog


def load_google_adk_catalog(
    tools: list[object],
    *,
    namespace: str | None = None,
) -> Catalog:
    """Convert a list of live Google ADK tool instances to a :class:`Catalog`.

    Reads ``name`` / ``description`` off each tool and resolves its argument
    schema from a ``_get_declaration()`` / ``get_declaration()`` method when
    present (ADK ``BaseTool`` exposes its schema via a ``FunctionDeclaration``),
    falling back to a ``parameters`` / ``args_schema`` attribute.  The framework
    dep is **not** imported by this module — the helper duck-types the inputs so
    callers can test the conversion path without the
    ``contextweaver[google-adk]`` extra installed.

    Args:
        tools: List of live ADK tool instances (or any object exposing
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
            raise CatalogError(f"Google ADK tool {tool!r} is missing a non-empty 'name' attribute.")
        if not isinstance(description, str) or not description:
            raise CatalogError(
                f"Google ADK tool {name!r} is missing a non-empty 'description' attribute."
            )
        tool_dicts.append(
            {
                "name": name,
                "description": description,
                "parameters": _declaration_parameters(tool),
                "tags": list(getattr(tool, "tags", None) or []),
                "is_long_running": getattr(tool, "is_long_running", None),
            }
        )
    return google_adk_tools_to_catalog(tool_dicts, namespace=namespace)


def _declaration_parameters(tool: object) -> object:
    """Resolve a live ADK tool's argument schema for conversion."""
    for fn_name in ("_get_declaration", "get_declaration"):
        getter = getattr(tool, fn_name, None)
        if callable(getter):
            try:
                declaration = getter()
            except Exception:  # pragma: no cover - defensive; depends on user tool
                declaration = None
            params = getattr(declaration, "parameters", None)
            if params is not None:
                return params
    return getattr(tool, "parameters", None) or getattr(tool, "args_schema", None)


def from_google_adk_session(
    session_or_events: object,
    into: ContextManager | None = None,
) -> list[ContextItem]:
    """Convert a Google ADK session's events into :class:`ContextItem`s.

    Accepts a ``Session`` exposing ``events`` or a plain list of event
    dicts/objects.  Mapping rules (ADK events carry a ``Content`` with
    ``parts``):

    - a text part authored by the user → :data:`ItemKind.user_turn`.
    - a text part authored by the model/agent → :data:`ItemKind.agent_msg`.
    - a ``function_call`` part → :data:`ItemKind.tool_call` (text is the
      JSON-encoded args); the call id is preserved in metadata.
    - a ``function_response`` part → :data:`ItemKind.tool_result` with
      ``parent_id`` set to the originating call so dependency closure links
      the pair.

    Parent linkage uses the ADK-provided ``id`` on the ``function_call`` /
    ``function_response`` parts.  Hand-built events that omit those ids do not
    link the response back to its call (each falls back to a distinct
    index-derived id), so supply the call ids when constructing events by hand.

    Args:
        session_or_events: A ``Session`` instance or a list of events.
        into: Optional :class:`~contextweaver.context.manager.ContextManager`
            to append each item to.

    Returns:
        A list of :class:`ContextItem` in event / part order.

    Raises:
        CatalogError: On malformed events or non-serialisable payloads.
    """
    return decode_session(session_or_events, into)


__all__ = [
    "from_google_adk_session",
    "google_adk_tool_to_selectable",
    "google_adk_tools_to_catalog",
    "infer_google_adk_namespace",
    "load_google_adk_catalog",
    "selectable_from_google_adk_tool",
]

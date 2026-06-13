"""Microsoft Agent Framework adapter for contextweaver (issue #430).

Bridges the [Microsoft Agent Framework](https://github.com/microsoft/agent-framework)
(the successor to AutoGen and Semantic Kernel) to contextweaver-native types,
following the same pure-converter pattern as the CrewAI / Agno / smolagents /
Google ADK adapters.

Two surfaces:

1. **Tool catalog** — :func:`agent_framework_tool_to_selectable`,
   :func:`agent_framework_tools_to_catalog`, :func:`load_agent_framework_catalog`
   convert Agent Framework ``AIFunction`` tools (or the equivalent plain-dict
   shape) into :class:`~contextweaver.types.SelectableItem` objects so a
   Microsoft-stack agent can route through contextweaver's bounded-choice
   router instead of dumping every tool definition into the prompt.  Schemas
   are held for hydration, never embedded in cards (the ChoiceCard invariant).

2. **Thread ingestion** — :func:`from_agent_framework_thread` maps a thread's
   ``ChatMessage`` sequence to :class:`~contextweaver.types.ContextItem`s with
   ``parent_id`` links so dependency closure includes a function call when its
   result is selected, and large tool results flow through the firewall.

The plain-dict / message-dict paths work without the ``agent-framework``
package installed; the live helpers accept real Agent Framework objects when
the ``contextweaver[agent-framework]`` optional extra is installed.  This module
imports no SDK at load time — it duck-types its inputs.

Out of scope (per issue #430): .NET support, the framework's workflow /
orchestration features, and replacing its memory abstractions — only the tool
and thread-history surfaces.

Agent Framework docs: https://github.com/microsoft/agent-framework
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from contextweaver.adapters._agent_framework_thread import decode_thread
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

if TYPE_CHECKING:
    from contextweaver.context.manager import ContextManager
    from contextweaver.types import ContextItem

logger = logging.getLogger("contextweaver.adapters")

_FALLBACK_NS = "agent_framework"
_ID_PREFIX = "agent_framework"


def infer_agent_framework_namespace(tool_name: str) -> str:
    """Infer a namespace from a Microsoft Agent Framework tool name.

    Falls back to ``"agent_framework"`` when no dot- / slash- / underscore-
    separated prefix can be detected.

    Args:
        tool_name: The raw tool name string.

    Returns:
        The inferred namespace string.
    """
    return infer_namespace(tool_name, fallback=_FALLBACK_NS)


def _parameters_value(tool_def: dict[str, Any]) -> object:
    """Pick the schema source from an Agent Framework tool dict.

    ``parameters`` (the JSON Schema an ``AIFunction`` exposes) wins;
    ``input_schema`` and ``args_schema`` are accepted as aliases.
    """
    for key in ("parameters", "input_schema", "args_schema"):
        value = tool_def.get(key)
        if value is not None:
            return value
    return None


def agent_framework_tool_to_selectable(
    tool_def: dict[str, Any],
    *,
    namespace: str | None = None,
) -> SelectableItem:
    """Convert a Microsoft Agent Framework tool definition to a :class:`SelectableItem`.

    The dict shape mirrors an Agent Framework ``AIFunction``:

    - ``name`` (required): the tool's display name.
    - ``description`` (required): natural-language description for the LLM.
    - ``parameters`` (optional): the JSON Schema for the tool's args, or a
      Pydantic ``BaseModel`` class (converted via ``model_json_schema()``).
    - ``input_schema`` / ``args_schema`` (optional aliases): used when
      ``parameters`` is absent.
    - ``tags`` (optional): list of tag strings.

    Args:
        tool_def: Raw tool definition dict.
        namespace: Explicit namespace override.  When ``None``, the namespace
            is inferred from the tool name via
            :func:`infer_agent_framework_namespace`.

    Returns:
        A :class:`SelectableItem` with ``kind="tool"`` and an ``id`` of
        ``"agent_framework:{name}"``.

    Raises:
        CatalogError: If required fields (``name``, ``description``) are
            missing or non-string.
    """
    raw_name, raw_description = require_name_description(tool_def, label="Agent Framework")

    ns = namespace if namespace is not None else infer_agent_framework_namespace(raw_name)
    short_name = strip_namespace_prefix(raw_name, ns)

    tags = collect_tags(tool_def.get("tags"), fallback=_FALLBACK_NS)
    args_schema = coerce_schema_dict(_parameters_value(tool_def))

    logger.debug(
        "agent_framework_tool_to_selectable: name=%s, ns=%s, tags=%s",
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
    )


# Alias matching the issue #430 spelling.
selectable_from_agent_framework_tool = agent_framework_tool_to_selectable


def agent_framework_tools_to_catalog(
    tools: list[dict[str, Any]],
    *,
    namespace: str | None = None,
) -> Catalog:
    """Convert a list of Agent Framework tool definitions to a populated :class:`Catalog`.

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
        catalog.register(agent_framework_tool_to_selectable(tool_def, namespace=namespace))
    logger.debug("agent_framework_tools_to_catalog: registered %d items", len(tools))
    return catalog


def load_agent_framework_catalog(
    tools: list[object],
    *,
    namespace: str | None = None,
) -> Catalog:
    """Convert a list of live Agent Framework tool instances to a :class:`Catalog`.

    Reads ``name`` / ``description`` off each tool and resolves its argument
    schema from a ``parameters`` / ``input_schema`` attribute (an
    ``AIFunction`` exposes its JSON Schema there).  The framework dep is
    **not** imported by this module — the helper duck-types the inputs so
    callers can test the conversion path without the
    ``contextweaver[agent-framework]`` extra installed.

    Args:
        tools: List of live Agent Framework tool instances (or any object
            exposing ``name`` / ``description`` attributes).
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
                f"Agent Framework tool {tool!r} is missing a non-empty 'name' attribute."
            )
        if not isinstance(description, str) or not description:
            raise CatalogError(
                f"Agent Framework tool {name!r} is missing a non-empty 'description' attribute."
            )
        tool_dicts.append(
            {
                "name": name,
                "description": description,
                "parameters": getattr(tool, "parameters", None)
                or getattr(tool, "input_schema", None),
                "tags": list(getattr(tool, "tags", None) or []),
            }
        )
    return agent_framework_tools_to_catalog(tool_dicts, namespace=namespace)


def from_agent_framework_thread(
    thread_or_messages: object,
    into: ContextManager | None = None,
) -> list[ContextItem]:
    """Convert a Microsoft Agent Framework thread into :class:`ContextItem`s.

    Accepts a thread exposing ``messages`` or a plain list of ``ChatMessage``
    objects / dicts.  Mapping rules (a message carries a ``role`` and a list of
    ``contents``):

    - a text content authored by the user → :data:`ItemKind.user_turn`.
    - a text content authored by the assistant / system → :data:`ItemKind.agent_msg`.
    - a ``FunctionCallContent`` → :data:`ItemKind.tool_call` (text is the
      JSON-encoded arguments); the call id is preserved in metadata.
    - a ``FunctionResultContent`` → :data:`ItemKind.tool_result` with
      ``parent_id`` set to the originating call so dependency closure links
      the pair.

    Parent linkage uses the ``call_id`` carried on the function-call /
    function-result contents.  Hand-built messages that omit those ids do not
    link the result back to its call (each falls back to a distinct
    index-derived id), so supply the call ids when constructing messages by hand.

    Args:
        thread_or_messages: A thread instance or a list of messages.
        into: Optional :class:`~contextweaver.context.manager.ContextManager`
            to append each item to.

    Returns:
        A list of :class:`ContextItem` in message / content order.

    Raises:
        CatalogError: On malformed messages or non-serialisable payloads.
    """
    return decode_thread(thread_or_messages, into)


__all__ = [
    "agent_framework_tool_to_selectable",
    "agent_framework_tools_to_catalog",
    "from_agent_framework_thread",
    "infer_agent_framework_namespace",
    "load_agent_framework_catalog",
    "selectable_from_agent_framework_tool",
]

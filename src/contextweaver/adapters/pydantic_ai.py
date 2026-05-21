"""Pydantic AI adapter for contextweaver (issue #272).

Bridges [Pydantic AI](https://ai.pydantic.dev/) tools and message history to
contextweaver-native types.  Converts Pydantic AI ``Tool`` definitions (or the
equivalent plain-dict shape Pydantic emits via ``model_dump()``) into
:class:`~contextweaver.types.SelectableItem` objects so agents built with
``pydantic_ai.Agent`` can route through contextweaver's bounded-choice router
instead of dumping every tool definition into the system prompt.

Two surfaces:

1. **Tool catalog** — :func:`pydantic_ai_tool_to_selectable`,
   :func:`pydantic_ai_tools_to_catalog`, :func:`load_pydantic_ai_catalog`.
   Mirrors :mod:`.crewai` (issue #193).

2. **Message round-trip** — :func:`from_pydantic_ai_messages` /
   :func:`to_pydantic_ai_messages`.  Mirrors :mod:`.openai_messages`
   (issue #219).  Operates on plain dicts following the Pydantic AI
   message-part schema; the live ``pydantic_ai.messages`` types serialise
   to this shape via ``model_dump()``.

Mapping rules for messages:

- ``kind="request"`` containing a ``user-prompt`` part → :data:`ItemKind.user_turn`.
- ``kind="request"`` containing a ``system-prompt`` part → :data:`ItemKind.policy`.
- ``kind="request"`` containing a ``tool-return`` part → :data:`ItemKind.tool_result`
  with ``parent_id`` set to the originating ``tool_call_id``.
- ``kind="response"`` containing a ``text`` part → :data:`ItemKind.agent_msg`.
- ``kind="response"`` containing a ``tool-call`` part → :data:`ItemKind.tool_call`.

``tool_call_id`` round-trips to/from :attr:`ContextItem.id` so
:func:`to_pydantic_ai_messages` is the inverse of
:func:`from_pydantic_ai_messages` for any well-formed input.

The plain-dict conversion functions work without the ``pydantic-ai``
package installed; the live :func:`load_pydantic_ai_catalog` helper accepts
real ``pydantic_ai.Tool`` instances when the ``contextweaver[pydantic-ai]``
optional extra is installed.

Pydantic AI message types: https://ai.pydantic.dev/api/messages/
Pydantic AI tool docs:     https://ai.pydantic.dev/tools/
"""

from __future__ import annotations

import copy
import logging
from typing import TYPE_CHECKING, Any

from contextweaver.adapters._pydantic_ai_messages import (
    decode_messages as _decode_messages,
)
from contextweaver.adapters._pydantic_ai_messages import (
    encode_messages as _encode_messages,
)
from contextweaver.exceptions import CatalogError
from contextweaver.routing.catalog import Catalog
from contextweaver.types import ContextItem, SelectableItem

if TYPE_CHECKING:
    from contextweaver.context.manager import ContextManager

logger = logging.getLogger("contextweaver.adapters")

_FALLBACK_NS = "pydantic_ai"
_ID_PREFIX = "pydantic_ai"


# ---------------------------------------------------------------------------
# Tool → SelectableItem
# ---------------------------------------------------------------------------


def infer_pydantic_ai_namespace(tool_name: str) -> str:
    """Infer a namespace from a Pydantic AI tool name.

    Uses the same dot- / slash- / underscore-separated prefix rules that the
    FastMCP and CrewAI adapters use, falling back to ``"pydantic_ai"`` when no
    prefix can be detected.

    Args:
        tool_name: The raw tool name string.

    Returns:
        The inferred namespace string.
    """
    if not tool_name:
        return _FALLBACK_NS
    for sep in (".", "/"):
        if sep in tool_name:
            prefix = tool_name.split(sep, 1)[0]
            if prefix:
                return prefix
    parts = tool_name.split("_")
    if len(parts) >= 2 and parts[0] and not parts[0].startswith("_"):
        return parts[0]
    return _FALLBACK_NS


def _strip_namespace_prefix(tool_name: str, namespace: str) -> str:
    """Return the short tool name with the namespace prefix removed."""
    for prefix in (f"{namespace}_", f"{namespace}.", f"{namespace}/"):
        if tool_name.startswith(prefix) and len(tool_name) > len(prefix):
            return tool_name[len(prefix) :]
    return tool_name


def _args_schema_dict(raw: object) -> dict[str, Any]:
    """Coerce a Pydantic AI ``parameters_json_schema`` value into a dict.

    Pydantic AI tools typically expose their argument schema as an already-
    realised JSON Schema dict via ``parameters_json_schema``.  Some user code
    builds the tool from a Pydantic ``BaseModel`` class — we accept either.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return copy.deepcopy(raw)
    schema_fn = getattr(raw, "model_json_schema", None)
    if callable(schema_fn):
        try:
            schema = schema_fn()
        except Exception:  # pragma: no cover - defensive
            return {}
        if isinstance(schema, dict):
            return dict(schema)
    return {}


def pydantic_ai_tool_to_selectable(
    tool_def: dict[str, Any],
    *,
    namespace: str | None = None,
) -> SelectableItem:
    """Convert a Pydantic AI tool definition dict to a :class:`SelectableItem`.

    The dict shape mirrors the field set on ``pydantic_ai.Tool``:

    - ``name`` (required): the tool's display name.
    - ``description`` (required): natural-language description for the LLM.
    - ``parameters_json_schema`` (optional): pre-built JSON Schema dict,
      or a Pydantic ``BaseModel`` class which is converted via
      ``model_json_schema()``.
    - ``args_schema`` (optional alias): accepted for symmetry with the
      CrewAI adapter — when both are present, ``parameters_json_schema``
      wins.
    - ``tags`` (optional): list of tag strings.

    Args:
        tool_def: Raw tool definition dict.
        namespace: Explicit namespace override.  When ``None``, the
            namespace is inferred from the tool name via
            :func:`infer_pydantic_ai_namespace`.

    Returns:
        A :class:`SelectableItem` with ``kind="tool"`` and an ``id`` of
        ``"pydantic_ai:{name}"``.

    Raises:
        CatalogError: If required fields (``name``, ``description``) are
            missing or non-string.
    """
    raw_name = tool_def.get("name")
    if not isinstance(raw_name, str) or not raw_name:
        raise CatalogError("Pydantic AI tool definition is missing a non-empty 'name' field.")
    raw_description = tool_def.get("description")
    if not isinstance(raw_description, str) or not raw_description:
        raise CatalogError(
            f"Pydantic AI tool {raw_name!r} is missing a non-empty 'description' field."
        )

    ns = namespace if namespace is not None else infer_pydantic_ai_namespace(raw_name)
    short_name = _strip_namespace_prefix(raw_name, ns)

    raw_tags = tool_def.get("tags")
    tags: set[str] = {_FALLBACK_NS}
    if isinstance(raw_tags, (list, set, tuple)):
        for tag in raw_tags:
            if isinstance(tag, str) and tag:
                tags.add(tag)

    schema_value = tool_def.get("parameters_json_schema", tool_def.get("args_schema"))
    args_schema = _args_schema_dict(schema_value)

    metadata: dict[str, Any] = {}
    if "takes_ctx" in tool_def:
        metadata["takes_ctx"] = bool(tool_def["takes_ctx"])
    if "max_retries" in tool_def and tool_def["max_retries"] is not None:
        metadata["max_retries"] = int(tool_def["max_retries"])
    if "strict" in tool_def:
        metadata["strict"] = bool(tool_def["strict"])

    logger.debug(
        "pydantic_ai_tool_to_selectable: name=%s, ns=%s, tags=%s",
        raw_name,
        ns,
        sorted(tags),
    )
    return SelectableItem(
        id=f"{_ID_PREFIX}:{raw_name}",
        kind="tool",
        name=short_name,
        description=raw_description,
        tags=sorted(tags),
        namespace=ns,
        args_schema=args_schema,
        metadata=metadata,
    )


# Alias matching the issue #272 spelling.
selectable_from_pydantic_tool = pydantic_ai_tool_to_selectable


def pydantic_ai_tools_to_catalog(
    tools: list[dict[str, Any]],
    *,
    namespace: str | None = None,
) -> Catalog:
    """Convert a list of Pydantic AI tool definitions to a populated :class:`Catalog`.

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
        catalog.register(pydantic_ai_tool_to_selectable(tool_def, namespace=namespace))
    logger.debug("pydantic_ai_tools_to_catalog: registered %d items", len(tools))
    return catalog


def load_pydantic_ai_catalog(
    tools: list[object],
    *,
    namespace: str | None = None,
) -> Catalog:
    """Convert a list of live ``pydantic_ai.Tool`` instances to a :class:`Catalog`.

    Each tool is dumped via Pydantic's ``model_dump()`` (or attribute access
    when ``model_dump`` is unavailable) and routed through
    :func:`pydantic_ai_tools_to_catalog`.  This helper does *not* import the
    ``pydantic_ai`` package itself — it duck-types the inputs so callers can
    test the conversion path without installing the optional extra.

    Args:
        tools: List of live Pydantic AI tool instances (or any object exposing
            ``name`` / ``description`` attributes plus an optional
            ``parameters_json_schema`` / ``args_schema``).
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
                raise CatalogError(f"Failed to dump Pydantic AI tool {tool!r}: {exc}") from exc
            if not isinstance(dumped, dict):
                raise CatalogError(f"Pydantic AI tool {tool!r}.model_dump() did not return a dict.")
            for attr in ("parameters_json_schema", "args_schema"):
                raw_val = getattr(tool, attr, None)
                if raw_val is not None and dumped.get(attr) in (None, {}):
                    dumped[attr] = raw_val
            tool_dicts.append(dumped)
        else:
            name = getattr(tool, "name", None)
            description = getattr(tool, "description", None)
            if not isinstance(name, str) or not name:
                raise CatalogError(
                    f"Pydantic AI tool {tool!r} is missing a non-empty 'name' attribute."
                )
            if not isinstance(description, str) or not description:
                raise CatalogError(
                    f"Pydantic AI tool {name!r} is missing a non-empty 'description' attribute."
                )
            tool_dicts.append(
                {
                    "name": name,
                    "description": description,
                    "parameters_json_schema": getattr(tool, "parameters_json_schema", None)
                    or getattr(tool, "args_schema", None),
                    "tags": list(getattr(tool, "tags", []) or []),
                }
            )
    return pydantic_ai_tools_to_catalog(tool_dicts, namespace=namespace)


# ---------------------------------------------------------------------------
# Messages → ContextItems
# ---------------------------------------------------------------------------


def from_pydantic_ai_messages(
    messages: list[dict[str, Any]],
    into: ContextManager | None = None,
) -> list[ContextItem]:
    """Convert Pydantic AI ``ModelMessage`` dicts into :class:`ContextItem`s.

    Args:
        messages: A list of Pydantic AI message dicts.  Each must have a
            ``kind`` of ``"request"`` or ``"response"`` and a ``parts`` list.
            Pass plain dicts (e.g. via ``ModelMessage.model_dump()``); the
            adapter does not import ``pydantic_ai`` at load time.
        into: Optional :class:`~contextweaver.context.manager.ContextManager`
            to append each item to.

    Returns:
        A list of :class:`ContextItem` in input message order.

    Raises:
        CatalogError: On unknown ``kind`` / ``part_kind`` values or missing
            ``tool_call_id`` on tool-related parts.
    """
    return _decode_messages(messages, into)


def to_pydantic_ai_messages(items: list[ContextItem]) -> list[dict[str, Any]]:
    """Inverse of :func:`from_pydantic_ai_messages`.

    Rebuilds the original Pydantic AI ``ModelMessage`` dict sequence from
    a list of :class:`ContextItem`s previously produced by
    :func:`from_pydantic_ai_messages`.  Round-trip is lossless for any
    well-formed input.

    Args:
        items: The items produced by a prior decode call.  Order is
            inferred from ``metadata["msg_index"]``; items without that key
            are skipped.

    Returns:
        A list of message dicts compatible with Pydantic AI's
        ``ModelMessage`` shape.
    """
    return _encode_messages(items)

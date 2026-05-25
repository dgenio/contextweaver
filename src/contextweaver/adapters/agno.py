"""Agno adapter for contextweaver (issue #275).

Bridges [Agno](https://github.com/agno-agi/agno) (formerly Phidata) ``Tool``
/ ``Function`` / ``Toolkit`` definitions and ``AgentSession`` message history
to contextweaver-native types.  Converts Agno tool definitions (or the
equivalent plain-dict shape that Agno's ``Function.to_dict()`` emits) into
:class:`SelectableItem` objects so agents built with ``agno.Agent`` can route
through contextweaver's bounded-choice router instead of dumping every tool
definition into the prompt.

Positioning note (per issue #275): contextweaver replaces only the
prompt-assembly step inside an Agno run.  It does **not** replace Agno's
``Memory`` / ``Storage`` / ``Knowledge`` layer — those remain authoritative
for long-lived state.  See ``docs/integration_agno.md`` for the layered
diagram.

Two surfaces:

1. **Tool catalog** — :func:`agno_tool_to_selectable`,
   :func:`agno_tools_to_catalog`, :func:`load_agno_catalog`.

2. **Session ingestion** — :func:`from_agno_session` reads an
   ``AgentSession.runs[*].messages`` chain (or any iterable of equivalent
   message dicts) and produces :class:`ContextItem`s.

The plain-dict paths work without the ``agno`` package installed; the
live :func:`load_agno_catalog` and :func:`from_agno_agent` helpers accept
real Agno objects when the ``contextweaver[agno]`` optional extra is
installed.

Agno docs:  https://docs.agno.com/
"""

from __future__ import annotations

import copy
import logging
from typing import TYPE_CHECKING, Any

from contextweaver.adapters._messages_common import (
    expect_dict,
    expect_list,
    ingest_into_manager,
    json_args_dumps,
)
from contextweaver.exceptions import CatalogError
from contextweaver.routing.catalog import Catalog
from contextweaver.types import ContextItem, ItemKind, SelectableItem

if TYPE_CHECKING:
    from contextweaver.context.manager import ContextManager

logger = logging.getLogger("contextweaver.adapters")

_FALLBACK_NS = "agno"
_ID_PREFIX = "agno"


# ---------------------------------------------------------------------------
# Tool → SelectableItem
# ---------------------------------------------------------------------------


def infer_agno_namespace(tool_name: str) -> str:
    """Infer a namespace from an Agno tool name.

    Agno tools are commonly named in snake_case (e.g. ``duckduckgo_search``,
    ``yfinance_get_company_info``); the namespace is the first underscore-
    separated segment.  Falls back to ``"agno"`` when no prefix is detectable.

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


def _params_to_schema(raw: object) -> dict[str, Any]:
    """Coerce Agno's ``parameters`` payload into a JSON-Schema dict.

    Agno's ``Function.parameters`` is already an OpenAI-style JSON Schema
    (``{"type": "object", "properties": {...}, "required": [...]}``) by
    construction, but we accept a Pydantic ``BaseModel`` class too for
    callers that built the function manually.
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


def agno_tool_to_selectable(
    tool_def: dict[str, Any],
    *,
    namespace: str | None = None,
) -> SelectableItem:
    """Convert an Agno tool / function definition dict to a :class:`SelectableItem`.

    The dict shape mirrors the field set on ``agno.tools.Function`` and the
    OpenAI ``function`` JSON-Schema shape Agno emits for tool calls:

    - ``name`` (required): the tool's display name.
    - ``description`` (required): natural-language description for the LLM.
    - ``parameters`` (optional): OpenAI-style JSON Schema dict.
    - ``args_schema`` (optional alias): accepted for symmetry with the
      CrewAI adapter — when both are present, ``parameters`` wins.
    - ``tags`` (optional): list of tag strings.
    - ``toolkit_name`` (optional): the Agno ``Toolkit`` that owns this
      function; when present and ``namespace`` is not explicitly set, it
      becomes the inferred namespace.

    Args:
        tool_def: Raw tool definition dict.
        namespace: Explicit namespace override.  When ``None``, the
            namespace is the ``toolkit_name`` if present, else inferred
            from the tool name via :func:`infer_agno_namespace`.

    Returns:
        A :class:`SelectableItem` with ``kind="tool"`` and an ``id`` of
        ``"agno:{name}"``.

    Raises:
        CatalogError: If required fields (``name``, ``description``) are
            missing or non-string.
    """
    raw_name = tool_def.get("name")
    if not isinstance(raw_name, str) or not raw_name:
        raise CatalogError("Agno tool definition is missing a non-empty 'name' field.")
    raw_description = tool_def.get("description")
    if not isinstance(raw_description, str) or not raw_description:
        raise CatalogError(f"Agno tool {raw_name!r} is missing a non-empty 'description' field.")

    if namespace is not None:
        ns = namespace
    else:
        toolkit = tool_def.get("toolkit_name")
        ns = toolkit if isinstance(toolkit, str) and toolkit else infer_agno_namespace(raw_name)
    short_name = _strip_namespace_prefix(raw_name, ns)

    raw_tags = tool_def.get("tags")
    tags: set[str] = {_FALLBACK_NS}
    if isinstance(raw_tags, (list, set, tuple)):
        for tag in raw_tags:
            if isinstance(tag, str) and tag:
                tags.add(tag)

    schema_value = tool_def.get("parameters", tool_def.get("args_schema"))
    args_schema = _params_to_schema(schema_value)

    metadata: dict[str, Any] = {}
    for meta_key in ("strict", "show_result", "stop_after_tool_call", "requires_confirmation"):
        if meta_key in tool_def and tool_def[meta_key] is not None:
            metadata[meta_key] = bool(tool_def[meta_key])
    if "toolkit_name" in tool_def and isinstance(tool_def["toolkit_name"], str):
        metadata["toolkit_name"] = tool_def["toolkit_name"]

    logger.debug(
        "agno_tool_to_selectable: name=%s, ns=%s, tags=%s",
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


# Alias matching the issue #275 spelling.
selectable_from_agno_tool = agno_tool_to_selectable


def agno_tools_to_catalog(
    tools: list[dict[str, Any]],
    *,
    namespace: str | None = None,
) -> Catalog:
    """Convert a list of Agno tool definitions to a populated :class:`Catalog`.

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
        catalog.register(agno_tool_to_selectable(tool_def, namespace=namespace))
    logger.debug("agno_tools_to_catalog: registered %d items", len(tools))
    return catalog


def load_agno_catalog(
    tools_or_toolkits: list[object],
    *,
    namespace: str | None = None,
) -> Catalog:
    """Convert a list of live Agno tools / toolkits to a :class:`Catalog`.

    Accepts any mix of:

    - ``Function`` instances (read ``name`` / ``description`` / ``parameters``).
    - ``Toolkit`` instances (read ``functions`` dict; each value is a Function).
    - Bare callables decorated with ``@tool`` (read ``__name__`` / ``__doc__``).

    The framework dep is **not** imported by this module — the helper duck-
    types the inputs so callers can test the conversion path without the
    ``contextweaver[agno]`` extra installed.

    Args:
        tools_or_toolkits: List of Agno ``Function`` / ``Toolkit`` instances
            or bare decorated callables.
        namespace: Optional namespace override applied to every item.

    Returns:
        A populated :class:`Catalog`.

    Raises:
        CatalogError: If any object is missing required attributes.
    """
    tool_dicts: list[dict[str, Any]] = []
    for entry in tools_or_toolkits:
        # Toolkit? walk its functions dict.
        functions = getattr(entry, "functions", None)
        if isinstance(functions, dict) and functions:
            toolkit_name = getattr(entry, "name", None) or entry.__class__.__name__
            for fn in functions.values():
                tool_dicts.append(_function_to_dict(fn, toolkit_name))
            continue
        tool_dicts.append(_function_to_dict(entry, None))
    return agno_tools_to_catalog(tool_dicts, namespace=namespace)


def _function_to_dict(fn: object, toolkit_name: str | None) -> dict[str, Any]:
    """Coerce an Agno function-like object into a tool definition dict."""
    # 1. Plain dict already.
    if isinstance(fn, dict):
        out = dict(fn)
        if toolkit_name and "toolkit_name" not in out:
            out["toolkit_name"] = toolkit_name
        return out

    # 2. Object with ``to_dict`` / ``model_dump``.
    for fn_name in ("to_dict", "model_dump"):
        dumper = getattr(fn, fn_name, None)
        if callable(dumper):
            try:
                dumped = dumper()
            except Exception as exc:  # pragma: no cover - defensive
                raise CatalogError(f"Agno function {fn!r}.{fn_name}() raised: {exc}") from exc
            if isinstance(dumped, dict):
                if toolkit_name and "toolkit_name" not in dumped:
                    dumped["toolkit_name"] = toolkit_name
                return dumped

    # 3. Attribute access (Function / bare callable).
    name = getattr(fn, "name", None) or getattr(fn, "__name__", None)
    description = getattr(fn, "description", None) or getattr(fn, "__doc__", None) or ""
    if isinstance(description, str):
        description = description.strip()
    if not isinstance(name, str) or not name:
        raise CatalogError(f"Agno function {fn!r} is missing a 'name' attribute (or '__name__').")
    if not isinstance(description, str) or not description:
        raise CatalogError(
            f"Agno function {name!r} is missing a 'description' attribute (or docstring)."
        )
    out_dict: dict[str, Any] = {
        "name": name,
        "description": description,
        "parameters": getattr(fn, "parameters", None) or getattr(fn, "args_schema", None),
        "tags": list(getattr(fn, "tags", []) or []),
    }
    if toolkit_name:
        out_dict["toolkit_name"] = toolkit_name
    return out_dict


# ---------------------------------------------------------------------------
# Session ingestion
# ---------------------------------------------------------------------------


def from_agno_session(
    session_or_messages: object,
    into: ContextManager | None = None,
) -> list[ContextItem]:
    """Convert an Agno ``AgentSession`` / message history into :class:`ContextItem`s.

    Accepts either:

    - An ``AgentSession`` / ``AgentRun`` instance (reads ``.messages`` or
      ``.runs[*].messages``).
    - A plain ``list`` of Agno message dicts following the OpenAI Chat
      Completions message shape that Agno emits via
      ``AgentSession.to_dict()``.

    Mapping rules (Agno follows the OpenAI message shape closely):

    - ``role="system"``    → :data:`ItemKind.policy`.
    - ``role="user"``      → :data:`ItemKind.user_turn`.
    - ``role="assistant"`` with ``content`` → :data:`ItemKind.agent_msg`.
    - ``role="assistant"`` with ``tool_calls`` → :data:`ItemKind.tool_call`
      per call.
    - ``role="tool"`` with ``tool_call_id`` → :data:`ItemKind.tool_result`
      with ``parent_id`` set to the originating ``tool_call_id``.

    Args:
        session_or_messages: An ``AgentSession`` / ``AgentRun`` instance, or a
            list of message dicts.
        into: Optional :class:`~contextweaver.context.manager.ContextManager`
            to append items to.

    Returns:
        A list of :class:`ContextItem` in message order.

    Raises:
        CatalogError: On unknown roles, malformed tool calls, or orphan tool
            results.
    """
    messages = _collect_session_messages(session_or_messages)
    expect_list(messages, fn_name="from_agno_session")

    seen_tool_calls: set[str] = set()
    items: list[ContextItem] = []
    for idx, msg in enumerate(messages):
        expect_dict(msg, label=f"Agno message at index {idx}")
        role = msg.get("role")
        if role == "system":
            items.append(_make_system_item(idx, msg))
        elif role == "user":
            items.append(_make_user_item(idx, msg))
        elif role == "assistant":
            items.extend(_decode_assistant(idx, msg, seen_tool_calls))
        elif role == "tool":
            items.append(_decode_tool_result(idx, msg, seen_tool_calls))
        else:
            raise CatalogError(
                f"Agno message at index {idx} has unknown role {role!r} "
                "(expected one of 'system', 'user', 'assistant', 'tool')."
            )

    ingest_into_manager(items, into)
    return items


# Alias matching the issue #275 spelling.
from_agno_agent = from_agno_session


def _collect_session_messages(session_or_messages: object) -> list[Any]:
    """Walk an Agno session / run object to a flat message list."""
    if isinstance(session_or_messages, list):
        return list(session_or_messages)
    direct = getattr(session_or_messages, "messages", None)
    if isinstance(direct, list):
        return list(direct)
    runs = getattr(session_or_messages, "runs", None)
    if isinstance(runs, list):
        out: list[Any] = []
        for run in runs:
            run_msgs = (
                getattr(run, "messages", None) if not isinstance(run, dict) else run.get("messages")
            )
            if isinstance(run_msgs, list):
                out.extend(run_msgs)
        return out
    raise CatalogError(
        "from_agno_session could not locate a 'messages' or 'runs' iterable on the input."
    )


def _make_system_item(idx: int, msg: dict[str, Any]) -> ContextItem:
    content = msg.get("content", "")
    if not isinstance(content, str):
        content = str(content)
    return ContextItem(
        id=f"{_ID_PREFIX}:system:{idx}",
        kind=ItemKind.policy,
        text=content,
        metadata={"msg_index": idx, "provider": _ID_PREFIX},
    )


def _make_user_item(idx: int, msg: dict[str, Any]) -> ContextItem:
    content = msg.get("content", "")
    if not isinstance(content, str):
        content = str(content)
    return ContextItem(
        id=f"{_ID_PREFIX}:user:{idx}",
        kind=ItemKind.user_turn,
        text=content,
        metadata={"msg_index": idx, "provider": _ID_PREFIX},
    )


def _decode_assistant(
    idx: int,
    msg: dict[str, Any],
    seen_tool_calls: set[str],
) -> list[ContextItem]:
    items: list[ContextItem] = []
    content = msg.get("content")
    tool_calls = msg.get("tool_calls") or []
    if isinstance(content, str) and content:
        items.append(
            ContextItem(
                id=f"{_ID_PREFIX}:assistant:{idx}",
                kind=ItemKind.agent_msg,
                text=content,
                metadata={"msg_index": idx, "provider": _ID_PREFIX},
            )
        )
    if not isinstance(tool_calls, list):
        raise CatalogError(f"Agno assistant message at index {idx} has non-list 'tool_calls'.")
    for call_idx, call in enumerate(tool_calls):
        expect_dict(call, label=f"Agno tool_call {call_idx} in message {idx}")
        call_id = call.get("id") or call.get("tool_call_id")
        if not isinstance(call_id, str) or not call_id:
            raise CatalogError(f"Agno tool_call at message {idx} index {call_idx} is missing 'id'.")
        fn = call.get("function") or {}
        if not isinstance(fn, dict):
            raise CatalogError(
                f"Agno tool_call {call_id!r} 'function' must be a dict, got {type(fn).__name__}."
            )
        tool_name = fn.get("name", "")
        args_payload = fn.get("arguments", "{}")
        args_text = (
            args_payload
            if isinstance(args_payload, str)
            else json_args_dumps(args_payload, label=f"agno tool_call {call_id!r}")
        )
        seen_tool_calls.add(call_id)
        items.append(
            ContextItem(
                id=f"{_ID_PREFIX}:tool_call:{call_id}",
                kind=ItemKind.tool_call,
                text=args_text,
                metadata={
                    "msg_index": idx,
                    "provider": _ID_PREFIX,
                    "tool_name": tool_name,
                    "tool_call_id": call_id,
                },
            )
        )
    return items


def _decode_tool_result(
    idx: int,
    msg: dict[str, Any],
    seen_tool_calls: set[str],
) -> ContextItem:
    call_id = msg.get("tool_call_id")
    if not isinstance(call_id, str) or not call_id:
        raise CatalogError(f"Agno tool message at index {idx} is missing 'tool_call_id'.")
    if call_id not in seen_tool_calls:
        raise CatalogError(
            f"Agno tool message at index {idx} references unknown tool_call_id {call_id!r}."
        )
    content = msg.get("content", "")
    if not isinstance(content, str):
        content = json_args_dumps(content, label=f"agno tool_result {call_id!r}")
    return ContextItem(
        id=f"{_ID_PREFIX}:tool_result:{call_id}",
        kind=ItemKind.tool_result,
        text=content,
        parent_id=f"{_ID_PREFIX}:tool_call:{call_id}",
        metadata={
            "msg_index": idx,
            "provider": _ID_PREFIX,
            "tool_name": msg.get("name", ""),
            "tool_call_id": call_id,
        },
    )

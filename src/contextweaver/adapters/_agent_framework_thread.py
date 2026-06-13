"""Thread ingestion helpers for the Microsoft Agent Framework adapter (issue #430).

Private module backing
:func:`contextweaver.adapters.agent_framework.from_agent_framework_thread`; kept
separate so ``agent_framework.py`` stays within the ≤300-line module ceiling.
Not public API.

Converts a Microsoft Agent Framework thread — a sequence of ``ChatMessage``
objects, each carrying a ``role`` and a list of ``contents`` (``TextContent`` /
``FunctionCallContent`` / ``FunctionResultContent``) — into
:class:`~contextweaver.types.ContextItem`s.  Function-result content links back
to its originating function call via ``parent_id`` so dependency closure
includes the call when its result is selected.  The decoder is dict-shaped so
it runs without the ``agent-framework`` SDK installed; live objects are coerced
via ``to_dict`` / ``model_dump`` / attribute access first.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from contextweaver.adapters._messages_common import (
    expect_list,
    ingest_into_manager,
    json_args_dumps,
)
from contextweaver.exceptions import CatalogError
from contextweaver.types import ContextItem, ItemKind

if TYPE_CHECKING:
    from contextweaver.context.manager import ContextManager

logger = logging.getLogger("contextweaver.adapters")

_ID_PREFIX = "agent_framework"


def _to_dict(obj: object) -> Any:  # noqa: ANN401 — opaque SDK object
    """Best-effort coercion of an Agent Framework object to a dict."""
    if isinstance(obj, dict):
        return obj
    for fn_name in ("model_dump", "to_dict"):
        fn = getattr(obj, fn_name, None)
        if callable(fn):
            try:
                dumped = fn()
            except Exception as exc:  # pragma: no cover - defensive
                raise CatalogError(
                    f"Agent Framework object {obj!r}.{fn_name}() raised: {exc}"
                ) from exc
            if isinstance(dumped, dict):
                return dumped
    return obj


def _collect_messages(thread_or_messages: object) -> list[Any]:
    """Locate the message list on a thread, a list, or a ``.messages`` holder."""
    if isinstance(thread_or_messages, list):
        return list(thread_or_messages)
    messages = getattr(thread_or_messages, "messages", None)
    if isinstance(messages, list):
        return list(messages)
    raise CatalogError(
        "from_agent_framework_thread could not locate a 'messages' iterable on the input."
    )


def _role_of(message: dict[str, Any]) -> str:
    """Normalise the message role to a plain lowercase string."""
    role = message.get("role")
    # Agent Framework's ``Role`` is an enum-like with a ``.value``.
    value = getattr(role, "value", role)
    return str(value or "").lower()


def _kind_for_role(role: str) -> ItemKind:
    return ItemKind.user_turn if role == "user" else ItemKind.agent_msg


def _contents(message: dict[str, Any]) -> list[Any]:
    raw = message.get("contents")
    if isinstance(raw, list):
        return raw
    return []


def decode_thread(
    thread_or_messages: object,
    into: ContextManager | None = None,
) -> list[ContextItem]:
    """Convert an Agent Framework thread / message list into :class:`ContextItem`s.

    See :func:`contextweaver.adapters.agent_framework.from_agent_framework_thread`
    for the public docstring and mapping rules.
    """
    raw_messages = _collect_messages(thread_or_messages)
    expect_list(raw_messages, fn_name="from_agent_framework_thread")

    items: list[ContextItem] = []
    for idx, raw_message in enumerate(raw_messages):
        message = _to_dict(raw_message)
        if not isinstance(message, dict):
            raise CatalogError(f"Agent Framework message at index {idx} is not a dict-like object.")
        role = _role_of(message)
        contents = _contents(message)
        if contents:
            for part_idx, raw_part in enumerate(contents):
                part = _to_dict(raw_part)
                if isinstance(part, dict):
                    item = _decode_part(idx, part_idx, role, part)
                    if item is not None:
                        items.append(item)
        else:
            # Some messages carry a bare ``text`` instead of ``contents``.
            text = message.get("text")
            if isinstance(text, str) and text.strip():
                items.append(_text_item(idx, 0, role, text))

    ingest_into_manager(items, into)
    return items


def _decode_part(idx: int, part_idx: int, role: str, part: dict[str, Any]) -> ContextItem | None:
    """Decode one content part into a :class:`ContextItem` (or ``None``)."""
    if part.get("name") is not None and ("arguments" in part or "args" in part):
        return _decode_function_call(idx, part_idx, part)
    if "call_id" in part and ("result" in part or "output" in part):
        return _decode_function_result(idx, part_idx, part)
    text = part.get("text")
    if isinstance(text, str) and text.strip():
        return _text_item(idx, part_idx, role, text)
    return None


def _text_item(idx: int, part_idx: int, role: str, text: str) -> ContextItem:
    return ContextItem(
        id=f"{_ID_PREFIX}:text:{idx}:{part_idx}",
        kind=_kind_for_role(role),
        text=text,
        metadata={"event_index": idx, "provider": _ID_PREFIX, "role": role},
    )


def _call_id_of(part: dict[str, Any], idx: int, part_idx: int) -> str:
    call_id = part.get("call_id") or part.get("id")
    if not isinstance(call_id, str) or not call_id:
        call_id = f"{_ID_PREFIX}-call-{idx}-{part_idx}"
    return call_id


def _decode_function_call(idx: int, part_idx: int, part: dict[str, Any]) -> ContextItem:
    call_id = _call_id_of(part, idx, part_idx)
    args_payload = part.get("arguments", part.get("args", {}))
    args_text = (
        args_payload
        if isinstance(args_payload, str)
        else json_args_dumps(args_payload, label=f"agent_framework function_call {call_id!r}")
    )
    return ContextItem(
        id=f"{_ID_PREFIX}:tool_call:{call_id}",
        kind=ItemKind.tool_call,
        text=args_text,
        metadata={
            "event_index": idx,
            "provider": _ID_PREFIX,
            "tool_name": part.get("name") or "",
            "tool_call_id": call_id,
        },
    )


def _decode_function_result(idx: int, part_idx: int, part: dict[str, Any]) -> ContextItem:
    call_id = _call_id_of(part, idx, part_idx)
    payload = part.get("result", part.get("output", ""))
    text = (
        payload
        if isinstance(payload, str)
        else json_args_dumps(payload, label=f"agent_framework function_result {call_id!r}")
    )
    return ContextItem(
        id=f"{_ID_PREFIX}:tool_result:{call_id}",
        kind=ItemKind.tool_result,
        text=text,
        parent_id=f"{_ID_PREFIX}:tool_call:{call_id}",
        metadata={
            "event_index": idx,
            "provider": _ID_PREFIX,
            "tool_name": part.get("name") or "",
            "tool_call_id": call_id,
        },
    )

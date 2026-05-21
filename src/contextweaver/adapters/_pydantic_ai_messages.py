"""Decode + encode helpers for the Pydantic AI message adapter.

Private module — its API is not part of the public contract.  The public
surface is re-exported from :mod:`contextweaver.adapters.pydantic_ai`.

Issue #272 (slice of #193).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from contextweaver.adapters._messages_common import (
    expect_dict,
    expect_list,
    ingest_into_manager,
    json_args_dumps,
)
from contextweaver.exceptions import CatalogError
from contextweaver.types import ContextItem, ItemKind

if TYPE_CHECKING:
    from contextweaver.context.manager import ContextManager

logger = logging.getLogger("contextweaver.adapters")

_ID_PREFIX = "pydantic_ai"
_PREFIX_TOOL_CALL = f"{_ID_PREFIX}:tool_call:"
_PREFIX_TOOL_RESULT = f"{_ID_PREFIX}:tool_result:"
_PREFIX_USER = f"{_ID_PREFIX}:user:"
_PREFIX_ASSISTANT = f"{_ID_PREFIX}:assistant:"
_PREFIX_SYSTEM = f"{_ID_PREFIX}:system:"


def _user_item(msg_idx: int, content: str) -> ContextItem:
    return ContextItem(
        id=f"{_PREFIX_USER}{msg_idx}",
        kind=ItemKind.user_turn,
        text=content,
        metadata={"msg_index": msg_idx, "provider": _ID_PREFIX},
    )


def _system_item(msg_idx: int, content: str) -> ContextItem:
    return ContextItem(
        id=f"{_PREFIX_SYSTEM}{msg_idx}",
        kind=ItemKind.policy,
        text=content,
        metadata={"msg_index": msg_idx, "provider": _ID_PREFIX},
    )


def _assistant_text_item(msg_idx: int, content: str) -> ContextItem:
    return ContextItem(
        id=f"{_PREFIX_ASSISTANT}{msg_idx}",
        kind=ItemKind.agent_msg,
        text=content,
        metadata={"msg_index": msg_idx, "provider": _ID_PREFIX},
    )


def _tool_call_item(
    msg_idx: int, call_id: str, tool_name: str, args_payload: object
) -> ContextItem:
    args_text = json_args_dumps(args_payload, label=f"tool-call {call_id!r}")
    return ContextItem(
        id=f"{_PREFIX_TOOL_CALL}{call_id}",
        kind=ItemKind.tool_call,
        text=args_text,
        metadata={
            "msg_index": msg_idx,
            "provider": _ID_PREFIX,
            "tool_name": tool_name,
            "tool_call_id": call_id,
        },
    )


def _tool_result_item(msg_idx: int, call_id: str, tool_name: str, content: str) -> ContextItem:
    return ContextItem(
        id=f"{_PREFIX_TOOL_RESULT}{call_id}",
        kind=ItemKind.tool_result,
        text=content,
        parent_id=f"{_PREFIX_TOOL_CALL}{call_id}",
        metadata={
            "msg_index": msg_idx,
            "provider": _ID_PREFIX,
            "tool_name": tool_name,
            "tool_call_id": call_id,
        },
    )


def _decode_request_parts(msg_idx: int, parts: list[Any]) -> list[ContextItem]:
    items: list[ContextItem] = []
    for part in parts:
        expect_dict(part, label=f"Pydantic AI request part at index {msg_idx}")
        part_kind = part.get("part_kind") or part.get("kind")
        if part_kind == "user-prompt":
            content = part.get("content", "")
            if not isinstance(content, str):
                content = str(content)
            items.append(_user_item(msg_idx, content))
        elif part_kind == "system-prompt":
            content = part.get("content", "")
            if not isinstance(content, str):
                content = str(content)
            items.append(_system_item(msg_idx, content))
        elif part_kind == "tool-return":
            call_id = part.get("tool_call_id")
            if not isinstance(call_id, str) or not call_id:
                raise CatalogError(
                    f"Pydantic AI tool-return part at index {msg_idx} is missing 'tool_call_id'."
                )
            tool_name = part.get("tool_name") or ""
            content = part.get("content", "")
            if not isinstance(content, str):
                content = json_args_dumps(content, label=f"tool-return {call_id!r}")
            items.append(_tool_result_item(msg_idx, call_id, tool_name, content))
        elif part_kind == "retry-prompt":
            content = part.get("content", "")
            if not isinstance(content, str):
                content = str(content)
            items.append(
                ContextItem(
                    id=f"{_ID_PREFIX}:retry:{msg_idx}",
                    kind=ItemKind.user_turn,
                    text=content,
                    metadata={
                        "msg_index": msg_idx,
                        "provider": _ID_PREFIX,
                        "retry": True,
                    },
                )
            )
        else:
            raise CatalogError(
                f"Pydantic AI request part at index {msg_idx} has unknown part_kind {part_kind!r}"
            )
    return items


def _decode_response_parts(msg_idx: int, parts: list[Any]) -> list[ContextItem]:
    items: list[ContextItem] = []
    for part in parts:
        expect_dict(part, label=f"Pydantic AI response part at index {msg_idx}")
        part_kind = part.get("part_kind") or part.get("kind")
        if part_kind == "text":
            content = part.get("content", "")
            if not isinstance(content, str):
                content = str(content)
            items.append(_assistant_text_item(msg_idx, content))
        elif part_kind == "tool-call":
            call_id = part.get("tool_call_id")
            if not isinstance(call_id, str) or not call_id:
                raise CatalogError(
                    f"Pydantic AI tool-call part at index {msg_idx} is missing 'tool_call_id'."
                )
            tool_name = part.get("tool_name") or ""
            args_payload = part.get("args", {})
            items.append(_tool_call_item(msg_idx, call_id, tool_name, args_payload))
        else:
            raise CatalogError(
                f"Pydantic AI response part at index {msg_idx} has unknown part_kind {part_kind!r}"
            )
    return items


def decode_messages(
    messages: list[dict[str, Any]],
    into: ContextManager | None,
) -> list[ContextItem]:
    """Convert Pydantic AI ``ModelMessage`` dicts into :class:`ContextItem`s."""
    expect_list(messages, fn_name="from_pydantic_ai_messages")

    seen_tool_calls: set[str] = set()
    items: list[ContextItem] = []
    for idx, msg in enumerate(messages):
        expect_dict(msg, label=f"Pydantic AI message at index {idx}")
        kind = msg.get("kind") or msg.get("message_kind")
        parts = msg.get("parts", [])
        if not isinstance(parts, list):
            raise CatalogError(f"Pydantic AI message at index {idx} 'parts' must be a list.")
        if kind == "request":
            decoded = _decode_request_parts(idx, parts)
            for item in decoded:
                if item.kind is ItemKind.tool_result:
                    call_id = item.metadata.get("tool_call_id")
                    if call_id not in seen_tool_calls:
                        raise CatalogError(
                            f"Pydantic AI tool-return at index {idx} references unknown "
                            f"tool_call_id {call_id!r}"
                        )
            items.extend(decoded)
        elif kind == "response":
            decoded = _decode_response_parts(idx, parts)
            for item in decoded:
                if item.kind is ItemKind.tool_call:
                    tool_call_id = item.metadata.get("tool_call_id")
                    if isinstance(tool_call_id, str):
                        seen_tool_calls.add(tool_call_id)
            items.extend(decoded)
        else:
            raise CatalogError(
                f"Pydantic AI message at index {idx} has unknown kind {kind!r} "
                "(expected 'request' or 'response')."
            )

    ingest_into_manager(items, into)
    return items


def _encode_request_part(item: ContextItem) -> dict[str, Any]:
    meta = item.metadata or {}
    if item.kind is ItemKind.user_turn:
        if meta.get("retry"):
            return {"part_kind": "retry-prompt", "content": item.text}
        return {"part_kind": "user-prompt", "content": item.text}
    if item.kind is ItemKind.policy:
        return {"part_kind": "system-prompt", "content": item.text}
    if item.kind is ItemKind.tool_result:
        return {
            "part_kind": "tool-return",
            "tool_name": meta.get("tool_name", ""),
            "tool_call_id": meta.get("tool_call_id", ""),
            "content": item.text,
        }
    raise CatalogError(
        f"Cannot encode {item.kind!r} as a Pydantic AI request part (id={item.id!r})."
    )


def _restore_args(text: str) -> object:
    """Re-parse a JSON-encoded args string back to its native Python shape.

    Falls back to the raw string when ``text`` is not valid JSON — matches
    Pydantic AI's ``ToolCallPart.args`` accepting either ``str`` or
    ``dict[str, Any]``.
    """
    try:
        parsed: object = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return text
    return parsed


def _encode_response_part(item: ContextItem) -> dict[str, Any]:
    meta = item.metadata or {}
    if item.kind is ItemKind.agent_msg:
        return {"part_kind": "text", "content": item.text}
    if item.kind is ItemKind.tool_call:
        return {
            "part_kind": "tool-call",
            "tool_name": meta.get("tool_name", ""),
            "tool_call_id": meta.get("tool_call_id", ""),
            "args": _restore_args(item.text),
        }
    raise CatalogError(
        f"Cannot encode {item.kind!r} as a Pydantic AI response part (id={item.id!r})."
    )


def encode_messages(items: list[ContextItem]) -> list[dict[str, Any]]:
    """Inverse of :func:`decode_messages`."""
    groups: dict[int, list[ContextItem]] = {}
    for item in items:
        meta = item.metadata or {}
        msg_idx = meta.get("msg_index")
        if msg_idx is None or meta.get("provider") != _ID_PREFIX:
            continue
        groups.setdefault(int(msg_idx), []).append(item)

    messages: list[dict[str, Any]] = []
    for idx in sorted(groups):
        group = groups[idx]
        if not group:
            continue
        first_kind = group[0].kind
        if first_kind in (ItemKind.user_turn, ItemKind.policy, ItemKind.tool_result):
            parts = [_encode_request_part(item) for item in group]
            messages.append({"kind": "request", "parts": parts})
        else:
            parts = [_encode_response_part(item) for item in group]
            messages.append({"kind": "response", "parts": parts})
    return messages

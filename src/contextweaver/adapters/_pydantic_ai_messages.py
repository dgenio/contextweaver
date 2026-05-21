"""Private decode/encode helpers for the Pydantic AI message adapter.

Used by :mod:`contextweaver.adapters.pydantic_ai`; not part of the public
API and not re-exported from ``contextweaver.adapters``.

Pydantic AI's ``ModelMessage`` is a discriminated union over
``ModelRequest`` (carrying user / system / tool-return parts) and
``ModelResponse`` (carrying assistant-text / tool-call parts).  Each part
self-identifies via a ``part_kind`` discriminator.  This module collapses
that nested shape to a flat list of :class:`ContextItem` and back.
"""

from __future__ import annotations

from typing import Any

from contextweaver.adapters._messages_common import json_args_dumps
from contextweaver.exceptions import CatalogError
from contextweaver.types import ContextItem, ItemKind

# Stable id prefixes used for round-tripping messages through ContextItem.id.
_PREFIX_SYSTEM = "pydantic_ai:system:"
_PREFIX_USER = "pydantic_ai:user:"
_PREFIX_ASSISTANT = "pydantic_ai:assistant:"
_PREFIX_TOOL_CALL = "pydantic_ai:tool_call:"
_PREFIX_TOOL_RESULT = "pydantic_ai:tool_result:"

# Pydantic AI emits these ``part_kind`` discriminators on the wire; the
# tuple form lets us accept minor spelling variations across releases.
_PART_KIND_SYSTEM = ("system-prompt", "system")
_PART_KIND_USER = ("user-prompt", "text-user")
_PART_KIND_TOOL_CALL = ("tool-call",)
_PART_KIND_TOOL_RETURN = ("tool-return",)
_PART_KIND_ASSISTANT = ("text", "assistant", "response")


def msg_dump(value: Any) -> dict[str, Any]:  # noqa: ANN401 — provider JSON
    """Return a plain dict from either a dict or a ``model_dump``-able object."""
    if isinstance(value, dict):
        return value
    dump_fn = getattr(value, "model_dump", None)
    if callable(dump_fn):
        try:
            dumped = dump_fn()
        except Exception as exc:  # pragma: no cover - defensive
            raise CatalogError(f"Pydantic AI message {value!r} failed to dump: {exc}") from exc
        if isinstance(dumped, dict):
            return dumped
    raise CatalogError(f"Pydantic AI message {value!r} is not a dict or model_dump-able.")


def part_to_item(
    kind: str,
    part: dict[str, Any],
    msg_idx: int,
    part_idx: int,
    label: str,
    seen_tool_call_ids: set[str],
) -> ContextItem:
    """Convert one Pydantic AI message part to a :class:`ContextItem`."""
    meta: dict[str, Any] = {
        "msg_index": msg_idx,
        "part_index": part_idx,
        "part_kind": kind,
    }
    if kind in _PART_KIND_SYSTEM:
        return ContextItem(
            id=f"{_PREFIX_SYSTEM}{msg_idx}:{part_idx}",
            kind=ItemKind.policy,
            text=str(part.get("content", "")),
            metadata=meta,
        )
    if kind in _PART_KIND_USER:
        return ContextItem(
            id=f"{_PREFIX_USER}{msg_idx}:{part_idx}",
            kind=ItemKind.user_turn,
            text=str(part.get("content", "")),
            metadata=meta,
        )
    if kind in _PART_KIND_ASSISTANT:
        return ContextItem(
            id=f"{_PREFIX_ASSISTANT}{msg_idx}:{part_idx}",
            kind=ItemKind.agent_msg,
            text=str(part.get("content", "")),
            metadata=meta,
        )
    if kind in _PART_KIND_TOOL_CALL:
        tool_call_id = str(part.get("tool_call_id") or f"{msg_idx}:{part_idx}")
        seen_tool_call_ids.add(tool_call_id)
        tool_name = str(part.get("tool_name", ""))
        args_payload = part.get("args")
        # Pydantic AI's ``ToolCallPart.args`` accepts either a dict or a
        # pre-stringified JSON payload (the LLM emits the latter); pass
        # strings through verbatim to avoid double-encoding on the
        # round-trip and dump dict / list payloads deterministically.
        if args_payload is None:
            args_str = ""
        elif isinstance(args_payload, str):
            args_str = args_payload
        else:
            args_str = json_args_dumps(args_payload, label=label)
        meta.update({"tool_call_id": tool_call_id, "tool_name": tool_name, "args": args_str})
        return ContextItem(
            id=f"{_PREFIX_TOOL_CALL}{tool_call_id}",
            kind=ItemKind.tool_call,
            text=f"{tool_name}({args_str})",
            metadata=meta,
        )
    if kind in _PART_KIND_TOOL_RETURN:
        tool_call_id = str(part.get("tool_call_id") or "")
        if not tool_call_id:
            raise CatalogError(f"{label} (tool-return) is missing 'tool_call_id'.")
        if tool_call_id not in seen_tool_call_ids:
            raise CatalogError(f"{label} references unknown tool_call_id={tool_call_id!r}")
        meta.update({"tool_call_id": tool_call_id, "tool_name": str(part.get("tool_name", ""))})
        return ContextItem(
            id=f"{_PREFIX_TOOL_RESULT}{tool_call_id}",
            kind=ItemKind.tool_result,
            text=str(part.get("content", "")),
            metadata=meta,
            parent_id=f"{_PREFIX_TOOL_CALL}{tool_call_id}",
        )
    raise CatalogError(f"{label} has unknown part_kind: {kind!r}")


def item_to_part(item: ContextItem) -> dict[str, Any]:
    """Render a :class:`ContextItem` back to a Pydantic AI message part dict."""
    meta = item.metadata or {}
    kind = meta.get("part_kind", "")
    if item.kind is ItemKind.policy:
        return {"part_kind": kind or "system-prompt", "content": item.text}
    if item.kind is ItemKind.user_turn:
        return {"part_kind": kind or "user-prompt", "content": item.text}
    if item.kind is ItemKind.agent_msg:
        return {"part_kind": kind or "text", "content": item.text}
    if item.kind is ItemKind.tool_call:
        return {
            "part_kind": kind or "tool-call",
            "tool_call_id": meta.get("tool_call_id", ""),
            "tool_name": meta.get("tool_name", ""),
            "args": meta.get("args", ""),
        }
    if item.kind is ItemKind.tool_result:
        return {
            "part_kind": kind or "tool-return",
            "tool_call_id": meta.get("tool_call_id", ""),
            "tool_name": meta.get("tool_name", ""),
            "content": item.text,
        }
    raise CatalogError(
        f"to_pydantic_ai_messages cannot serialise ContextItem of kind "
        f"{item.kind.value!r} (id={item.id!r})"
    )


def classify_message_kind(items: list[ContextItem]) -> str:
    """Pick the message-level discriminator for a group of items.

    Pydantic AI uses ``"response"`` for messages carrying assistant text or
    tool-call parts, and ``"request"`` for everything else (system / user /
    tool-return).
    """
    response_kinds = {ItemKind.agent_msg, ItemKind.tool_call}
    return "response" if any(it.kind in response_kinds for it in items) else "request"

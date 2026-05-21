"""Agno message-history adapter for contextweaver (issue #275).

Pure stateless converter between an
[Agno](https://github.com/agno-agi/agno) ``Agent``'s recorded message
history and contextweaver's :class:`~contextweaver.types.ContextItem`
event log.

Agno stores conversation history under ``Agent.memory.messages`` and the
per-run history on ``RunResponse.messages``.  Each ``Message`` follows
the OpenAI Chat-Completions shape with a small Agno extension:
``role``, ``content``, ``tool_calls`` (with the standard ``id`` /
``function.name`` / ``function.arguments``), ``tool_call_id`` for
``role="tool"`` rows, plus optional ``name`` and ``reasoning_content``.

.. code-block:: python

    from contextweaver.adapters.agno_messages import from_agno_agent

    items = from_agno_agent(agent, into=mgr)

The decoder accepts either a live ``agno.agent.Agent`` (anything
exposing ``.memory.messages`` or a ``.run_response.messages`` attribute)
or an explicit ``list[dict]`` of OpenAI-shaped message dicts.  No
``agno`` import is required for the dict path.
"""

from __future__ import annotations

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

_PREFIX_SYSTEM = "agno:system:"
_PREFIX_USER = "agno:user:"
_PREFIX_ASSISTANT = "agno:assistant:"
_PREFIX_TOOL_CALL = "agno:tool_call:"
_PREFIX_TOOL_RESULT = "agno:tool_result:"


def _extract_messages(agent: Any) -> list[dict[str, Any]]:  # noqa: ANN401 — provider object
    """Pull a list of message dicts out of either a live agent or a raw list."""
    if isinstance(agent, list):
        return [_msg_dump(m) for m in agent]
    # Check the per-run response first (richer than session memory).
    run_response = getattr(agent, "run_response", None)
    if run_response is not None:
        msgs = getattr(run_response, "messages", None)
        if isinstance(msgs, list):
            return [_msg_dump(m) for m in msgs]
    memory = getattr(agent, "memory", None)
    if memory is not None:
        msgs = getattr(memory, "messages", None)
        if isinstance(msgs, list):
            return [_msg_dump(m) for m in msgs]
        get_messages_fn = getattr(memory, "get_messages", None)
        if callable(get_messages_fn):
            try:
                raw = get_messages_fn()
            except Exception as exc:  # pragma: no cover - defensive
                raise CatalogError(f"Agno memory.get_messages() failed: {exc}") from exc
            if isinstance(raw, list):
                return [_msg_dump(m) for m in raw]
    raise CatalogError(
        f"Agno agent {agent!r} exposes neither .run_response.messages nor "
        ".memory.messages / .memory.get_messages()"
    )


def _msg_dump(value: Any) -> dict[str, Any]:  # noqa: ANN401 — provider object
    if isinstance(value, dict):
        return value
    dump_fn = getattr(value, "model_dump", None) or getattr(value, "to_dict", None)
    if callable(dump_fn):
        try:
            dumped = dump_fn()
        except Exception as exc:  # pragma: no cover - defensive
            raise CatalogError(f"Agno message {value!r} failed to dump: {exc}") from exc
        if isinstance(dumped, dict):
            return dumped
    raise CatalogError(f"Agno message {value!r} is not dict-convertible")


def from_agno_agent(
    agent: Any,  # noqa: ANN401 — accepts live Agent or list[dict]
    into: ContextManager | None = None,
) -> list[ContextItem]:
    """Convert an Agno ``Agent``'s message history into ContextItems.

    Args:
        agent: A live ``agno.agent.Agent`` (anything exposing
            ``.run_response.messages`` or ``.memory.messages`` /
            ``.memory.get_messages()``) or an explicit ``list[dict]`` of
            OpenAI-shaped message dicts.
        into: Optional :class:`ContextManager`.  When provided, every
            produced item is appended to the manager's event log in
            order.

    Returns:
        A list of :class:`ContextItem` in input message order.  Assistant
        messages with ``tool_calls`` expand into one ``agent_msg`` item
        followed by one ``tool_call`` item per call; ``role="tool"``
        rows become ``tool_result`` items linked back via ``parent_id``.

    Raises:
        CatalogError: If a message is missing required fields, has an
            unknown role, or a ``role="tool"`` entry omits
            ``tool_call_id``.
    """
    messages = _extract_messages(agent)
    expect_list(messages, fn_name="from_agno_agent")

    items: list[ContextItem] = []
    seen_tool_call_ids: set[str] = set()
    for idx, msg in enumerate(messages):
        expect_dict(msg, label=f"Agno message at index {idx}")
        role = msg.get("role")
        if role == "system":
            items.append(_decode_system(msg, idx))
        elif role == "user":
            items.append(_decode_user(msg, idx))
        elif role == "assistant":
            items.extend(_decode_assistant(msg, idx, seen_tool_call_ids))
        elif role == "tool":
            items.append(_decode_tool(msg, idx, seen_tool_call_ids))
        else:
            raise CatalogError(f"Agno message at index {idx} has unknown role: {role!r}")

    ingest_into_manager(items, into)
    logger.debug("from_agno_agent: messages_in=%d, items_out=%d", len(messages), len(items))
    return items


def _decode_system(msg: dict[str, Any], idx: int) -> ContextItem:
    return ContextItem(
        id=f"{_PREFIX_SYSTEM}{idx}",
        kind=ItemKind.policy,
        text=str(msg.get("content") or ""),
        metadata={"msg_index": idx, "role": "system"},
    )


def _decode_user(msg: dict[str, Any], idx: int) -> ContextItem:
    return ContextItem(
        id=f"{_PREFIX_USER}{idx}",
        kind=ItemKind.user_turn,
        text=str(msg.get("content") or ""),
        metadata={"msg_index": idx, "role": "user"},
    )


def _decode_assistant(
    msg: dict[str, Any], idx: int, seen_tool_call_ids: set[str]
) -> list[ContextItem]:
    items: list[ContextItem] = []
    content = msg.get("content")
    reasoning = msg.get("reasoning_content")
    text_parts: list[str] = []
    if isinstance(reasoning, str) and reasoning:
        text_parts.append(reasoning)
    if isinstance(content, str) and content:
        text_parts.append(content)
    text = "\n\n".join(text_parts)
    meta: dict[str, Any] = {"msg_index": idx, "role": "assistant"}
    if isinstance(reasoning, str) and reasoning:
        meta["has_reasoning"] = True
    items.append(
        ContextItem(
            id=f"{_PREFIX_ASSISTANT}{idx}",
            kind=ItemKind.agent_msg,
            text=text,
            metadata=meta,
        )
    )

    tool_calls = msg.get("tool_calls") or []
    if not isinstance(tool_calls, list):
        raise CatalogError(f"Agno assistant message at index {idx} has non-list 'tool_calls'.")
    for call_idx, tc in enumerate(tool_calls):
        expect_dict(tc, label=f"Agno tool_call at message {idx} index {call_idx}")
        items.append(_decode_tool_call(tc, idx, call_idx, seen_tool_call_ids))
    return items


def _decode_tool_call(
    tc: dict[str, Any], msg_idx: int, call_idx: int, seen_tool_call_ids: set[str]
) -> ContextItem:
    tool_call_id = str(tc.get("id") or "")
    if not tool_call_id:
        raise CatalogError(f"Agno tool_call at message {msg_idx} index {call_idx} is missing 'id'.")
    seen_tool_call_ids.add(tool_call_id)
    fn = tc.get("function") or {}
    if not isinstance(fn, dict):
        raise CatalogError(
            f"Agno tool_call at message {msg_idx} index {call_idx} has non-dict 'function'."
        )
    tool_name = str(fn.get("name") or "")
    args_payload = fn.get("arguments")
    # Agno serialises arguments as a JSON string already; pass through if
    # so, otherwise dump deterministically.
    if isinstance(args_payload, str):
        args_str = args_payload
    elif args_payload is None:
        args_str = ""
    else:
        args_str = json_args_dumps(
            args_payload,
            label=f"Agno tool_call at message {msg_idx} index {call_idx}",
        )
    return ContextItem(
        id=f"{_PREFIX_TOOL_CALL}{tool_call_id}",
        kind=ItemKind.tool_call,
        text=f"{tool_name}({args_str})",
        metadata={
            "msg_index": msg_idx,
            "call_index": call_idx,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "arguments": args_str,
        },
    )


def _decode_tool(msg: dict[str, Any], idx: int, seen_tool_call_ids: set[str]) -> ContextItem:
    tool_call_id = str(msg.get("tool_call_id") or "")
    if not tool_call_id:
        raise CatalogError(f"Agno tool message at index {idx} is missing 'tool_call_id'.")
    if tool_call_id not in seen_tool_call_ids:
        raise CatalogError(
            f"Agno tool message at index {idx} references unknown tool_call_id={tool_call_id!r}"
        )
    return ContextItem(
        id=f"{_PREFIX_TOOL_RESULT}{tool_call_id}",
        kind=ItemKind.tool_result,
        text=str(msg.get("content") or ""),
        metadata={
            "msg_index": idx,
            "tool_call_id": tool_call_id,
            "tool_name": str(msg.get("name") or ""),
        },
        parent_id=f"{_PREFIX_TOOL_CALL}{tool_call_id}",
    )

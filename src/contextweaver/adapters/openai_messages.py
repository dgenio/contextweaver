"""OpenAI Chat Completions message-array adapter for contextweaver.

Bridges the OpenAI Chat Completions ``messages`` schema and
contextweaver's :class:`~contextweaver.types.ContextItem` event log.
Users with an existing OpenAI agent can drop the library in with a single
call:

.. code-block:: python

    from contextweaver.context.manager import ContextManager
    from contextweaver.types import Phase
    from contextweaver.adapters.openai_messages import from_openai_messages

    mgr = ContextManager()
    from_openai_messages(messages, into=mgr)
    pack = mgr.build_sync(phase=Phase.answer, query=user_query)

The adapter is a pure stateless converter — no provider SDK is imported
at module load time (per the ``adapters/`` path convention in
``AGENTS.md``).  Operate on plain ``dict``s following the documented
OpenAI Chat Completions JSON schema; Pydantic / OpenAI-SDK callers
should convert via ``.model_dump()`` before calling.

Mapping rules:

- ``role="system"``    → :data:`ItemKind.policy`
- ``role="user"``      → :data:`ItemKind.user_turn`
- ``role="assistant"`` → :data:`ItemKind.agent_msg`
  (and one :data:`ItemKind.tool_call` per entry in ``tool_calls``)
- ``role="tool"`` with ``tool_call_id`` → :data:`ItemKind.tool_result`
  with ``parent_id`` set to the originating ``tool_call_id``.

``tool_call_id`` round-trips to/from ``ContextItem.id`` so
:func:`to_openai_messages` is the inverse of :func:`from_openai_messages`
for any well-formed input.

Issue #219 (slice of #194).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from contextweaver.exceptions import CatalogError
from contextweaver.types import ContextItem, ItemKind

if TYPE_CHECKING:
    from contextweaver.context.manager import ContextManager

logger = logging.getLogger("contextweaver.adapters")

# ID prefix carries enough information to round-trip the OpenAI message:
# - "openai:tool_call:<tool_call_id>" → the assistant's tool-call entry
# - "openai:tool_result:<tool_call_id>" → the matching role="tool" response
# - "openai:user:<idx>" / "openai:assistant:<idx>" / "openai:system:<idx>" → turn entries
_PREFIX_TOOL_CALL = "openai:tool_call:"
_PREFIX_TOOL_RESULT = "openai:tool_result:"
_PREFIX_USER = "openai:user:"
_PREFIX_ASSISTANT = "openai:assistant:"
_PREFIX_SYSTEM = "openai:system:"


# ---------------------------------------------------------------------------
# Public: from_openai_messages
# ---------------------------------------------------------------------------


def from_openai_messages(
    messages: list[dict[str, Any]],
    into: ContextManager | None = None,
) -> list[ContextItem]:
    """Convert OpenAI Chat Completions messages into ContextItems.

    Args:
        messages: A list of OpenAI Chat Completions message dicts. Each must
            have a ``role`` key (``"system"``, ``"user"``, ``"assistant"``,
            or ``"tool"``). ``content`` may be a plain string or ``None``
            (assistant messages with only ``tool_calls`` may omit content).
        into: Optional :class:`~contextweaver.context.manager.ContextManager`
            instance. When provided, every returned item is appended to the
            manager's event log via :meth:`ContextManager.ingest` in order.

    Returns:
        A list of :class:`ContextItem` in input message order.  Assistant
        messages with ``tool_calls`` expand into one ``agent_msg`` item
        followed by one ``tool_call`` item per call.

    Raises:
        CatalogError: If a message is missing required fields, has an
            unknown role, or a ``role="tool"`` entry omits ``tool_call_id``.
    """
    if not isinstance(messages, list):
        raise CatalogError(f"from_openai_messages expects a list, got {type(messages).__name__}")

    items: list[ContextItem] = []
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            raise CatalogError(f"OpenAI message at index {idx} is not a dict: {type(msg).__name__}")
        role = msg.get("role")
        if role == "system":
            items.append(_from_system(msg, idx))
        elif role == "user":
            items.append(_from_user(msg, idx))
        elif role == "assistant":
            items.extend(_from_assistant(msg, idx))
        elif role == "tool":
            items.append(_from_tool(msg, idx))
        else:
            raise CatalogError(f"OpenAI message at index {idx} has unknown role: {role!r}")

    if into is not None:
        for item in items:
            into.ingest(item)

    logger.debug("from_openai_messages: messages_in=%d, items_out=%d", len(messages), len(items))
    return items


# ---------------------------------------------------------------------------
# Public: to_openai_messages
# ---------------------------------------------------------------------------


def to_openai_messages(items: list[ContextItem]) -> list[dict[str, Any]]:
    """Convert ContextItems back into OpenAI Chat Completions messages.

    Inverse of :func:`from_openai_messages` — for any list produced by
    ``from_openai_messages(msgs)``, calling ``to_openai_messages(...)`` on
    it produces a list structurally equal to ``msgs``.

    Tool calls are re-assembled by walking consecutive
    :data:`ItemKind.agent_msg` + N :data:`ItemKind.tool_call` runs back
    into a single assistant message with a ``tool_calls`` array.

    Args:
        items: List of context items, typically produced by
            :func:`from_openai_messages`.

    Returns:
        A list of OpenAI Chat Completions message dicts.

    Raises:
        CatalogError: If a tool_call item lacks the required round-trip
            metadata (``tool_call_id``, ``function_name``, ``arguments``).
    """
    messages: list[dict[str, Any]] = []
    i = 0
    while i < len(items):
        item = items[i]
        if item.kind is ItemKind.policy:
            messages.append({"role": "system", "content": item.text})
        elif item.kind is ItemKind.user_turn:
            messages.append({"role": "user", "content": item.text})
        elif item.kind is ItemKind.agent_msg:
            # Walk forward and collect any directly-adjacent tool_call items
            # that originated from this assistant turn.
            tool_calls = _collect_assistant_tool_calls(items, i + 1)
            msg: dict[str, Any] = {"role": "assistant", "content": item.text or None}
            if tool_calls:
                msg["tool_calls"] = [tc["payload"] for tc in tool_calls]
                # Advance past the agent_msg AND the tool_call items we just
                # consumed; without this they would be re-emitted as orphans.
                i += len(tool_calls)
            messages.append(msg)
        elif item.kind is ItemKind.tool_call:
            # Orphan tool_call (no preceding agent_msg from this adapter) —
            # emit as a standalone assistant message with one tool_call.
            payload = _tool_call_payload(item)
            messages.append({"role": "assistant", "content": None, "tool_calls": [payload]})
        elif item.kind is ItemKind.tool_result:
            tool_call_id = item.metadata.get("tool_call_id") or _strip_prefix(
                item.id, _PREFIX_TOOL_RESULT
            )
            if not tool_call_id:
                raise CatalogError(
                    f"tool_result item {item.id!r} cannot round-trip: missing "
                    "tool_call_id in metadata and id does not carry the openai prefix"
                )
            messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": item.text})
        else:
            # Other ItemKind values (doc_snippet, memory_fact, plan_state) are
            # not produced by from_openai_messages; reject explicitly rather
            # than silently dropping.
            raise CatalogError(
                f"to_openai_messages cannot serialise ContextItem of kind "
                f"{item.kind.value!r} (id={item.id!r})"
            )
        i += 1
    return messages


# ---------------------------------------------------------------------------
# Per-role decoders
# ---------------------------------------------------------------------------


def _from_system(msg: dict[str, Any], idx: int) -> ContextItem:
    content = _string_content(msg, idx, "system")
    return ContextItem(
        id=f"{_PREFIX_SYSTEM}{idx}",
        kind=ItemKind.policy,
        text=content,
        metadata={"role": "system"},
    )


def _from_user(msg: dict[str, Any], idx: int) -> ContextItem:
    content = _string_content(msg, idx, "user")
    return ContextItem(
        id=f"{_PREFIX_USER}{idx}",
        kind=ItemKind.user_turn,
        text=content,
        metadata={"role": "user"},
    )


def _from_assistant(msg: dict[str, Any], idx: int) -> list[ContextItem]:
    """Expand an assistant message into agent_msg + N tool_call items."""
    out: list[ContextItem] = []
    raw_content = msg.get("content")
    # Assistant messages may have content=None when only tool_calls are present.
    text = "" if raw_content is None else str(raw_content)
    out.append(
        ContextItem(
            id=f"{_PREFIX_ASSISTANT}{idx}",
            kind=ItemKind.agent_msg,
            text=text,
            metadata={
                "role": "assistant",
                # Preserve None vs "" so to_openai_messages can re-emit None.
                "content_is_null": raw_content is None,
                # Tag both agent_msg and its child tool_calls with the same
                # input index so to_openai_messages can re-associate them.
                "assistant_idx": idx,
            },
        )
    )

    tool_calls = msg.get("tool_calls") or []
    if not isinstance(tool_calls, list):
        raise CatalogError(f"OpenAI assistant message at index {idx} has non-list tool_calls")

    for tc_idx, tc in enumerate(tool_calls):
        if not isinstance(tc, dict):
            raise CatalogError(
                f"OpenAI tool_call at index [{idx}].tool_calls[{tc_idx}] is not a dict"
            )
        tc_id = tc.get("id")
        if not tc_id:
            raise CatalogError(f"OpenAI tool_call at index [{idx}].tool_calls[{tc_idx}] missing id")
        fn = tc.get("function") or {}
        if not isinstance(fn, dict):
            raise CatalogError(f"OpenAI tool_call {tc_id!r} has non-dict function payload")
        fn_name = fn.get("name", "")
        # Arguments are a JSON-encoded string in OpenAI's schema. Preserve
        # the raw string in text for inspection AND store the parsed args
        # in metadata so to_openai_messages can re-emit the canonical shape.
        args_str = fn.get("arguments", "")
        if not isinstance(args_str, str):
            args_str = json.dumps(args_str)
        out.append(
            ContextItem(
                id=f"{_PREFIX_TOOL_CALL}{tc_id}",
                kind=ItemKind.tool_call,
                text=args_str,
                metadata={
                    "tool_call_id": tc_id,
                    "tool_call_type": tc.get("type", "function"),
                    "function_name": fn_name,
                    "arguments": args_str,
                    "assistant_idx": idx,
                },
            )
        )
    return out


def _from_tool(msg: dict[str, Any], idx: int) -> ContextItem:
    tool_call_id = msg.get("tool_call_id")
    if not tool_call_id:
        raise CatalogError(f"OpenAI tool message at index {idx} missing tool_call_id")
    content = _string_content(msg, idx, "tool")
    return ContextItem(
        id=f"{_PREFIX_TOOL_RESULT}{tool_call_id}",
        kind=ItemKind.tool_result,
        text=content,
        metadata={"role": "tool", "tool_call_id": tool_call_id},
        parent_id=f"{_PREFIX_TOOL_CALL}{tool_call_id}",
    )


# ---------------------------------------------------------------------------
# to_openai_messages helpers
# ---------------------------------------------------------------------------


def _collect_assistant_tool_calls(items: list[ContextItem], start: int) -> list[dict[str, Any]]:
    """Collect tool_call items that follow an assistant agent_msg.

    A tool_call belongs to the immediately-preceding agent_msg iff its
    ``metadata["assistant_idx"]`` matches the agent_msg's input index. We
    look at consecutive tool_call items and stop at the first non-match.
    """
    out: list[dict[str, Any]] = []
    if start == 0:
        return out
    prev = items[start - 1]
    if prev.kind is not ItemKind.agent_msg:
        return out
    expected_idx = prev.metadata.get("assistant_idx") if prev.metadata else None
    cursor = start
    while cursor < len(items):
        candidate = items[cursor]
        if candidate.kind is not ItemKind.tool_call:
            break
        # Only consume tool_calls that came from this assistant turn.
        cand_idx = candidate.metadata.get("assistant_idx") if candidate.metadata else None
        if cand_idx != expected_idx:
            break
        out.append({"payload": _tool_call_payload(candidate)})
        cursor += 1
    return out


def _tool_call_payload(item: ContextItem) -> dict[str, Any]:
    """Build an OpenAI ``tool_calls[]`` entry from a stored tool_call item."""
    tool_call_id = item.metadata.get("tool_call_id") if item.metadata else None
    if not tool_call_id:
        # Fall back to stripping the prefix; rejects items that weren't
        # produced by this adapter.
        tool_call_id = _strip_prefix(item.id, _PREFIX_TOOL_CALL)
    if not tool_call_id:
        raise CatalogError(
            f"tool_call item {item.id!r} cannot round-trip: missing "
            "tool_call_id metadata and id is not openai-prefixed"
        )
    fn_name = item.metadata.get("function_name", "") if item.metadata else ""
    arguments = item.metadata.get("arguments") if item.metadata else None
    if arguments is None:
        arguments = item.text
    return {
        "id": tool_call_id,
        "type": item.metadata.get("tool_call_type", "function") if item.metadata else "function",
        "function": {"name": fn_name, "arguments": arguments},
    }


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _string_content(msg: dict[str, Any], idx: int, role: str) -> str:
    """Coerce the ``content`` field to a string with a clear error on misuse."""
    content = msg.get("content")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    # OpenAI also supports `content` as a list of content parts (vision /
    # multimodal); we collapse the text parts and preserve unknown blocks
    # via JSON so callers see *something* rather than silently losing data.
    if isinstance(content, list):
        text_parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text_parts.append(str(part.get("text", "")))
            else:
                text_parts.append(json.dumps(part, sort_keys=True))
        return "\n".join(text_parts)
    raise CatalogError(
        f"OpenAI {role} message at index {idx} has unsupported content type: "
        f"{type(content).__name__}"
    )


def _strip_prefix(value: str, prefix: str) -> str:
    return value[len(prefix) :] if value.startswith(prefix) else ""

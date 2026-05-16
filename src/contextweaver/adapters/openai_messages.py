"""OpenAI Chat Completions message-array adapter for contextweaver.

Pure stateless converter between the OpenAI Chat Completions ``messages``
schema and contextweaver's :class:`~contextweaver.types.ContextItem`
event log. No provider SDK is imported at module load time (per the
``adapters/`` path convention in ``AGENTS.md``); operate on plain
``dict``s following the OpenAI Chat Completions JSON schema. Pydantic /
OpenAI-SDK callers should convert via ``.model_dump()`` first.

.. code-block:: python

    from contextweaver.context.manager import ContextManager
    from contextweaver.types import Phase
    from contextweaver.adapters.openai_messages import from_openai_messages

    mgr = ContextManager()
    from_openai_messages(messages, into=mgr)
    pack = mgr.build_sync(phase=Phase.answer, query=user_query)

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

from contextweaver.adapters._messages_common import (
    expect_dict,
    expect_list,
    ingest_into_manager,
    strip_prefix,
)
from contextweaver.exceptions import CatalogError
from contextweaver.types import ContextItem, ItemKind

if TYPE_CHECKING:
    from contextweaver.context.manager import ContextManager

logger = logging.getLogger("contextweaver.adapters")

# ID prefix carries enough information to round-trip the OpenAI message:
# - "openai:tool_call:<tool_call_id>"   → the assistant's tool-call entry
# - "openai:tool_result:<tool_call_id>" → the matching role="tool" response
# - "openai:{user,assistant,system}:<idx>" → turn entries
_PREFIX_TOOL_CALL = "openai:tool_call:"
_PREFIX_TOOL_RESULT = "openai:tool_result:"
_PREFIX_USER = "openai:user:"
_PREFIX_ASSISTANT = "openai:assistant:"
_PREFIX_SYSTEM = "openai:system:"


# --- Public: from_openai_messages ---


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
    expect_list(messages, fn_name="from_openai_messages")

    # Track tool_call_ids announced by prior assistant messages so a
    # subsequent role="tool" entry must reference one of them. Without
    # this check, an orphan tool result would leave a dangling parent_id
    # pointing at a non-existent ContextItem (PR #230 review).
    seen_tool_call_ids: set[str] = set()
    items: list[ContextItem] = []
    for idx, msg in enumerate(messages):
        expect_dict(msg, label=f"OpenAI message at index {idx}")
        role = msg.get("role")
        if role == "system":
            items.append(_from_system(msg, idx))
        elif role == "user":
            items.append(_from_user(msg, idx))
        elif role == "assistant":
            items.extend(_from_assistant(msg, idx, seen_tool_call_ids))
        elif role == "tool":
            items.append(_from_tool(msg, idx, seen_tool_call_ids))
        else:
            raise CatalogError(f"OpenAI message at index {idx} has unknown role: {role!r}")

    ingest_into_manager(items, into)
    logger.debug("from_openai_messages: messages_in=%d, items_out=%d", len(messages), len(items))
    return items


# --- Public: to_openai_messages ---


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
            meta = item.metadata or {}
            # Use stored metadata to distinguish content=None / "" / list-of-
            # parts at decode time. Without this, all three collapse to None
            # (or to a str) on the round-trip (PR #230 review).
            content_value = _restore_assistant_content(item, meta)
            msg: dict[str, Any] = {"role": "assistant", "content": content_value}
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
            tool_call_id = item.metadata.get("tool_call_id") or strip_prefix(
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


# --- Per-role decoders ---


def _from_system(msg: dict[str, Any], idx: int) -> ContextItem:
    return _make_turn(msg, idx, role="system", kind=ItemKind.policy, prefix=_PREFIX_SYSTEM)


def _from_user(msg: dict[str, Any], idx: int) -> ContextItem:
    return _make_turn(msg, idx, role="user", kind=ItemKind.user_turn, prefix=_PREFIX_USER)


def _make_turn(
    msg: dict[str, Any], idx: int, *, role: str, kind: ItemKind, prefix: str
) -> ContextItem:
    return ContextItem(
        id=f"{prefix}{idx}",
        kind=kind,
        text=_string_content(msg, idx, role),
        metadata={"role": role},
    )


def _from_assistant(
    msg: dict[str, Any], idx: int, seen_tool_call_ids: set[str]
) -> list[ContextItem]:
    """Expand an assistant message into agent_msg + N tool_call items.

    Metadata keys ``content_is_null`` and ``original_content`` preserve
    the three input shapes (``None`` / ``""`` / list-of-parts) so the
    inverse adapter can re-emit them exactly (PR #230 review).
    """
    out: list[ContextItem] = []
    raw_content = msg.get("content")
    text = "" if raw_content is None else _string_content(msg, idx, "assistant")
    out.append(
        ContextItem(
            id=f"{_PREFIX_ASSISTANT}{idx}",
            kind=ItemKind.agent_msg,
            text=text,
            metadata={
                "role": "assistant",
                "content_is_null": raw_content is None,
                "original_content": raw_content,
                "assistant_idx": idx,
            },
        )
    )

    tool_calls = msg.get("tool_calls") or []
    if not isinstance(tool_calls, list):
        raise CatalogError(f"OpenAI assistant message at index {idx} has non-list tool_calls")

    for tc_idx, tc in enumerate(tool_calls):
        loc = f"OpenAI tool_call at index [{idx}].tool_calls[{tc_idx}]"
        expect_dict(tc, label=loc)
        tc_id = tc.get("id")
        if not tc_id:
            raise CatalogError(f"{loc} missing id")
        fn = tc.get("function") or {}
        expect_dict(fn, label=f"OpenAI tool_call {tc_id!r} function payload")
        fn_name = fn.get("name", "")
        # Arguments are a JSON-encoded string in OpenAI's schema; preserve
        # raw text + parsed args in metadata so to_openai_messages can
        # re-emit the canonical shape.
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
        seen_tool_call_ids.add(tc_id)
    return out


def _from_tool(msg: dict[str, Any], idx: int, seen_tool_call_ids: set[str]) -> ContextItem:
    tool_call_id = msg.get("tool_call_id")
    if not tool_call_id:
        raise CatalogError(f"OpenAI tool message at index {idx} missing tool_call_id")
    if tool_call_id not in seen_tool_call_ids:
        # The tool_call_id must reference a prior assistant tool_calls entry
        # so the resulting ContextItem.parent_id points at a real item (PR
        # #230 review). Mirrors the Gemini adapter's unmatched-response check.
        raise CatalogError(
            f"OpenAI tool message at index {idx} has tool_call_id={tool_call_id!r} "
            "that does not match any prior assistant tool_calls entry"
        )
    content = _string_content(msg, idx, "tool")
    return ContextItem(
        id=f"{_PREFIX_TOOL_RESULT}{tool_call_id}",
        kind=ItemKind.tool_result,
        text=content,
        metadata={"role": "tool", "tool_call_id": tool_call_id},
        parent_id=f"{_PREFIX_TOOL_CALL}{tool_call_id}",
    )


# --- to_openai_messages helpers ---


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
        tool_call_id = strip_prefix(item.id, _PREFIX_TOOL_CALL)
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


# --- Shared helpers ---


def _restore_assistant_content(
    item: ContextItem, meta: dict[str, Any]
) -> Any:  # noqa: ANN401 — provider-shaped JSON
    """Reconstruct assistant ``content``: None / list / string (PR #230)."""
    if meta.get("content_is_null"):
        return None
    original = meta.get("original_content")
    if isinstance(original, list):
        return original
    return item.text


def _string_content(msg: dict[str, Any], idx: int, role: str) -> str:
    """Coerce ``content`` to a string; collapse list-of-parts to joined text.

    The list path supports OpenAI's multimodal `content` shape. The original
    list payload is preserved separately in metadata so the round-trip is
    lossless (see :func:`_from_assistant`).
    """
    content = msg.get("content")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(p.get("text", ""))
            if isinstance(p, dict) and p.get("type") == "text"
            else json.dumps(p, sort_keys=True)
            for p in content
        ]
        return "\n".join(parts)
    raise CatalogError(
        f"OpenAI {role} message at index {idx} has unsupported content type: "
        f"{type(content).__name__}"
    )

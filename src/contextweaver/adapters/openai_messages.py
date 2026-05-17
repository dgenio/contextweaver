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

The decoder helpers live in :mod:`._openai_decode` and the encoder
helpers in :mod:`._openai_encode` to keep this public surface focused
and within the repo's ≤300-line module guideline.

Issue #219 (slice of #194).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from contextweaver.adapters._messages_common import (
    expect_dict,
    expect_list,
    ingest_into_manager,
    strip_prefix,
)
from contextweaver.adapters._openai_decode import (
    _PREFIX_TOOL_RESULT,
    _from_assistant,
    _from_system,
    _from_tool,
    _from_user,
)
from contextweaver.adapters._openai_encode import (
    _collect_assistant_tool_calls,
    _restore_assistant_content,
    _tool_call_payload,
)
from contextweaver.exceptions import CatalogError
from contextweaver.types import ContextItem, ItemKind

if TYPE_CHECKING:
    from contextweaver.context.manager import ContextManager

logger = logging.getLogger("contextweaver.adapters")


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

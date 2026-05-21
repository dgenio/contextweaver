"""Pydantic AI message-array adapter for contextweaver (issue #272).

Pure stateless converter between the
[Pydantic AI](https://ai.pydantic.dev/api/messages/) message history
shape (``list[ModelMessage]``) and contextweaver's
:class:`~contextweaver.types.ContextItem` event log.

Pydantic AI represents history as a discriminated union over
``ModelRequest`` (carrying system / user / tool-return parts) and
``ModelResponse`` (carrying assistant-text / tool-call parts).  Each
part self-identifies via a ``part_kind`` discriminator.  The decoder
flattens those parts so each one becomes a single ``ContextItem``; the
encoder regroups items by ``metadata["msg_index"]`` and classifies each
group as ``"request"`` vs ``"response"``.

.. code-block:: python

    from contextweaver.context.manager import ContextManager
    from contextweaver.adapters.pydantic_ai_messages import from_pydantic_ai_messages

    mgr = ContextManager()
    from_pydantic_ai_messages(messages, into=mgr)

No ``pydantic_ai`` import is required: operate on plain dicts (the
``ModelMessage.model_dump()`` shape) or pass live ``ModelMessage``
instances and they are dumped on the fly.

The per-part decode/encode helpers live in
:mod:`._pydantic_ai_messages` to keep this module within the repo's
≤300-line guideline.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from contextweaver.adapters._messages_common import (
    expect_dict,
    expect_list,
    ingest_into_manager,
)
from contextweaver.adapters._pydantic_ai_messages import (
    classify_message_kind,
    item_to_part,
    msg_dump,
    part_to_item,
)
from contextweaver.exceptions import CatalogError
from contextweaver.types import ContextItem

if TYPE_CHECKING:
    from contextweaver.context.manager import ContextManager

logger = logging.getLogger("contextweaver.adapters")


def from_pydantic_ai_messages(
    messages: list[Any],
    into: ContextManager | None = None,
) -> list[ContextItem]:
    """Convert a Pydantic AI ``list[ModelMessage]`` into ContextItems.

    Args:
        messages: A list of ``ModelMessage`` instances (or their
            ``.model_dump()`` dict equivalents).
        into: Optional :class:`ContextManager`.  When provided, every
            produced item is appended to the manager's event log via
            :meth:`ContextManager.ingest` in order.

    Returns:
        A list of :class:`ContextItem` in input order.  Each Pydantic AI
        *part* becomes one item; multi-part messages expand to multiple
        items grouped by ``metadata["msg_index"]``.

    Raises:
        CatalogError: If a message is malformed, a part has an unknown
            ``part_kind``, or a ``tool-return`` references an unknown
            ``tool_call_id``.
    """
    expect_list(messages, fn_name="from_pydantic_ai_messages")
    items: list[ContextItem] = []
    seen_tool_call_ids: set[str] = set()
    for msg_idx, raw in enumerate(messages):
        msg = msg_dump(raw)
        expect_dict(msg, label=f"Pydantic AI message at index {msg_idx}")
        parts = msg.get("parts") or []
        if not isinstance(parts, list):
            raise CatalogError(f"Pydantic AI message at index {msg_idx} has non-list 'parts'.")
        for part_idx, part in enumerate(parts):
            expect_dict(
                part,
                label=f"Pydantic AI part at message {msg_idx} part {part_idx}",
            )
            kind = part.get("part_kind") or part.get("kind") or ""
            label = f"Pydantic AI part at message {msg_idx} part {part_idx}"
            items.append(part_to_item(kind, part, msg_idx, part_idx, label, seen_tool_call_ids))
    ingest_into_manager(items, into)
    logger.debug(
        "from_pydantic_ai_messages: messages_in=%d, items_out=%d",
        len(messages),
        len(items),
    )
    return items


def to_pydantic_ai_messages(items: list[ContextItem]) -> list[dict[str, Any]]:
    """Convert ContextItems back into Pydantic AI ``ModelMessage`` dicts.

    Inverse of :func:`from_pydantic_ai_messages` — for any list produced
    by ``from_pydantic_ai_messages(msgs)``, calling
    ``to_pydantic_ai_messages(...)`` on it produces a list structurally
    equal to ``msgs`` (modulo opaque vendor metadata that the decoder
    doesn't surface).

    Args:
        items: List of context items, typically produced by
            :func:`from_pydantic_ai_messages`.

    Returns:
        A list of Pydantic AI message dicts (``{"kind": ..., "parts":
        [...]}``).  Feed back into ``ModelMessagesTypeAdapter`` if you
        need live ``ModelMessage`` instances.

    Raises:
        CatalogError: If an item lacks the ``msg_index`` metadata
            required for round-tripping, or its kind cannot be rendered
            as a Pydantic AI part.
    """
    groups: dict[int, list[ContextItem]] = {}
    for item in items:
        meta = item.metadata or {}
        idx = meta.get("msg_index")
        if idx is None:
            raise CatalogError(
                f"ContextItem {item.id!r} missing 'msg_index' metadata; "
                "cannot round-trip back to Pydantic AI messages"
            )
        groups.setdefault(int(idx), []).append(item)

    output: list[dict[str, Any]] = []
    for idx in sorted(groups):
        members = sorted(groups[idx], key=lambda it: int((it.metadata or {}).get("part_index", 0)))
        output.append(
            {
                "kind": classify_message_kind(members),
                "parts": [item_to_part(it) for it in members],
            }
        )
    return output

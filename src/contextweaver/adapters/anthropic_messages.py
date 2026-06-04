"""Anthropic Messages-API message-array adapter for contextweaver.

Pure stateless converter between the Anthropic Messages API
``messages`` schema and contextweaver's :class:`~contextweaver.types.ContextItem`
event log. No provider SDK is imported at module load time; operate on
plain ``dict``s following the documented Anthropic Messages JSON schema.

.. code-block:: python

    from contextweaver.context.manager import ContextManager
    from contextweaver.adapters.anthropic_messages import from_anthropic_messages

    mgr = ContextManager()
    from_anthropic_messages(messages, into=mgr)

Anthropic differs from OpenAI's flat shape in two ways the adapter handles:

- ``content`` is **always a list of content blocks** (``text``,
  ``tool_use``, ``tool_result``); ``content: "..."`` is a string-shorthand
  the API normalises. The adapter preserves whichever shape the caller
  used so the round-trip is exact.
- IDs that thread tool calls and tool results live **inside** the content
  blocks (``tool_use.id`` ↔ ``tool_result.tool_use_id``).

Mapping (see ``AGENTS.md`` for the full ``ItemKind`` map):

- ``user`` text → :data:`ItemKind.user_turn`;
  ``assistant`` text → :data:`ItemKind.agent_msg`.
- ``assistant`` ``tool_use`` blocks → :data:`ItemKind.tool_call` items.
- ``user`` ``tool_result`` blocks → :data:`ItemKind.tool_result` items
  with ``parent_id`` pointing at the matching ``tool_use`` item.

System prompts (top-level API param, not a message) are out of scope.

The decoder helpers live in :mod:`._anthropic_decode` and the encoder
helpers in :mod:`._anthropic_encode` to keep this public surface focused
and within the repo's ≤300-line module guideline.

Issue #222 (closes #194 together with the OpenAI slice #219).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from contextweaver.adapters._anthropic_decode import (
    _blocks_to_items,
    _normalise_content,
)
from contextweaver.adapters._anthropic_encode import _block_index_sort_key, _item_to_block
from contextweaver.adapters._messages_common import (
    content_blocks_are_empty,
    expect_dict,
    expect_list,
    group_items_by_msg_index,
    ingest_into_manager,
    raise_empty_message_content,
)
from contextweaver.exceptions import CatalogError
from contextweaver.types import ContextItem

if TYPE_CHECKING:
    from contextweaver.context.manager import ContextManager

logger = logging.getLogger("contextweaver.adapters")


def from_anthropic_messages(
    messages: list[dict[str, Any]],
    into: ContextManager | None = None,
) -> list[ContextItem]:
    """Convert an Anthropic Messages API ``messages`` array into ContextItems.

    Args:
        messages: A list of Anthropic message dicts. Each must have ``role``
            (``"user"`` or ``"assistant"``) and ``content`` (either a plain
            string or a list of content-block dicts).
        into: Optional :class:`~contextweaver.context.manager.ContextManager`.
            When provided, each returned item is appended via
            :meth:`ContextManager.ingest` in order.

    Returns:
        A list of :class:`ContextItem` in input message order. Multi-block
        messages expand into multiple items (one per block); the block
        index is preserved in ``metadata["block_index"]`` so the inverse
        adapter can rebuild the original block order.

    Raises:
        CatalogError: On unknown role, unknown block ``type``, malformed
            input, or missing ``tool_use_id`` on a ``tool_result`` block.
    """
    expect_list(messages, fn_name="from_anthropic_messages")

    # Track tool_use ids announced by prior assistant turns so a subsequent
    # tool_result block must reference one of them. Without this check, an
    # orphan tool_result would leave a dangling parent_id (PR #230 review).
    seen_tool_use_ids: set[str] = set()
    items: list[ContextItem] = []
    for idx, msg in enumerate(messages):
        expect_dict(msg, label=f"Anthropic message at index {idx}")
        role = msg.get("role")
        if role not in ("user", "assistant"):
            raise CatalogError(f"Anthropic message at index {idx} has unknown role: {role!r}")
        blocks = _normalise_content(msg.get("content"), idx, role)
        original_is_string = isinstance(msg.get("content"), str)
        items.extend(_blocks_to_items(blocks, idx, role, original_is_string, seen_tool_use_ids))

    ingest_into_manager(items, into)
    logger.debug("from_anthropic_messages: messages_in=%d, items_out=%d", len(messages), len(items))
    return items


def to_anthropic_messages(items: list[ContextItem]) -> list[dict[str, Any]]:
    """Inverse of :func:`from_anthropic_messages`.

    Re-groups ``ContextItem``s carrying the same ``metadata["msg_index"]``
    back into a single Anthropic message and emits their original content
    blocks in original ``block_index`` order. If a message was originally
    a string shorthand, the inverse re-emits it as a string.

    Args:
        items: Items produced by :func:`from_anthropic_messages` (or any
            sequence carrying the same metadata).

    Returns:
        A list of Anthropic message dicts.

    Raises:
        CatalogError: If items are missing the round-trip metadata
            (``msg_index``, ``block_index``, ``role``), or if a message would
            serialise to empty content (empty / blank-text blocks), which the
            Anthropic API rejects with ``400 ... must have non-empty content``.
    """
    groups = group_items_by_msg_index(items, target_label="Anthropic messages")

    out: list[dict[str, Any]] = []
    for msg_idx in sorted(groups):
        group = sorted(groups[msg_idx], key=_block_index_sort_key)
        first_meta = group[0].metadata or {}
        role = first_meta.get("role")
        if role not in ("user", "assistant"):
            raise CatalogError(f"ContextItem group msg_index={msg_idx} has invalid role={role!r}")
        blocks = [_item_to_block(item) for item in group]
        # A user/assistant turn whose blocks render to nothing (empty list, or
        # only blank text blocks) would emit content="" / content=[] — which
        # the Anthropic API rejects with a 400. Fail fast at conversion time.
        if content_blocks_are_empty(
            [b.get("text") if b.get("type") == "text" else None for b in blocks]
        ):
            raise_empty_message_content(
                provider="Anthropic", locator=f"at msg_index={msg_idx}", role=role
            )
        was_string_shorthand = bool(first_meta.get("was_string_shorthand", False))
        if was_string_shorthand and len(blocks) == 1 and blocks[0].get("type") == "text":
            out.append({"role": role, "content": blocks[0]["text"]})
        else:
            out.append({"role": role, "content": blocks})
    return out

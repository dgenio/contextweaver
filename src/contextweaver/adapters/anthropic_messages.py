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

Issue #222 (closes #194 together with the OpenAI slice #219).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from contextweaver.adapters._messages_common import (
    expect_dict,
    expect_list,
    group_items_by_msg_index,
    ingest_into_manager,
    json_args_dumps,
    sort_key_by_meta_index,
)
from contextweaver.exceptions import CatalogError
from contextweaver.types import ContextItem, ItemKind

if TYPE_CHECKING:
    from contextweaver.context.manager import ContextManager

logger = logging.getLogger("contextweaver.adapters")


_PREFIX_USER = "anthropic:user:"
_PREFIX_ASSISTANT = "anthropic:assistant:"
_PREFIX_TOOL_USE = "anthropic:tool_use:"
_PREFIX_TOOL_RESULT = "anthropic:tool_result:"


# --- Public: from_anthropic_messages ---


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


# --- Public: to_anthropic_messages ---


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
            (``msg_index``, ``block_index``, ``role``).
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
        was_string_shorthand = bool(first_meta.get("was_string_shorthand", False))
        if was_string_shorthand and len(blocks) == 1 and blocks[0].get("type") == "text":
            out.append({"role": role, "content": blocks[0]["text"]})
        else:
            out.append({"role": role, "content": blocks})
    return out


# --- Decoding (messages → items) ---


def _normalise_content(
    content: Any,  # noqa: ANN401 — content is opaque provider JSON
    idx: int,
    role: str,
) -> list[dict[str, Any]]:
    """Coerce the ``content`` field to a list of content blocks."""
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        for b_idx, block in enumerate(content):
            expect_dict(block, label=f"Anthropic content block [{idx}][{b_idx}]")
        return list(content)
    raise CatalogError(
        f"Anthropic {role} message at index {idx} has unsupported content type: "
        f"{type(content).__name__}"
    )


def _blocks_to_items(
    blocks: list[dict[str, Any]],
    msg_idx: int,
    role: str,
    was_string_shorthand: bool,
    seen_tool_use_ids: set[str],
) -> list[ContextItem]:
    """Convert a normalised content-block list into one or more items."""
    out: list[ContextItem] = []
    for b_idx, block in enumerate(blocks):
        block_type = block.get("type")
        base_meta: dict[str, Any] = {
            "role": role,
            "msg_index": msg_idx,
            "block_index": b_idx,
            "block_type": block_type,
            # Only meaningful when this group has a single text block; we
            # tag every block so the inverse adapter sees it on group[0].
            "was_string_shorthand": was_string_shorthand,
            # Preserve the full original block so the inverse adapter can
            # re-emit unknown provider fields (e.g. `cache_control`) and
            # distinguish an explicit `is_error: False` from a missing one
            # (PR #230 review).
            "original_block": block,
        }
        if block_type == "text":
            text = str(block.get("text", ""))
            kind = ItemKind.user_turn if role == "user" else ItemKind.agent_msg
            prefix = _PREFIX_USER if role == "user" else _PREFIX_ASSISTANT
            out.append(
                ContextItem(
                    id=f"{prefix}{msg_idx}:{b_idx}",
                    kind=kind,
                    text=text,
                    metadata=base_meta,
                )
            )
        elif block_type == "tool_use":
            if role != "assistant":
                raise CatalogError(
                    f"tool_use block at [{msg_idx}][{b_idx}] must be on an "
                    f"assistant message, got role={role!r}"
                )
            tool_use_id = block.get("id")
            if not tool_use_id:
                raise CatalogError(f"Anthropic tool_use block at [{msg_idx}][{b_idx}] missing 'id'")
            tool_name = block.get("name", "")
            input_payload = block.get("input", {})
            args_str = json_args_dumps(
                input_payload, label=f"Anthropic tool_use.input at [{msg_idx}][{b_idx}]"
            )
            meta = {
                **base_meta,
                "tool_use_id": tool_use_id,
                "function_name": tool_name,
                "input": input_payload,
            }
            out.append(
                ContextItem(
                    id=f"{_PREFIX_TOOL_USE}{tool_use_id}",
                    kind=ItemKind.tool_call,
                    text=args_str,
                    metadata=meta,
                )
            )
            seen_tool_use_ids.add(tool_use_id)
        elif block_type == "tool_result":
            if role != "user":
                raise CatalogError(
                    f"tool_result block at [{msg_idx}][{b_idx}] must be on a "
                    f"user message, got role={role!r}"
                )
            tool_use_id = block.get("tool_use_id")
            if not tool_use_id:
                raise CatalogError(
                    f"Anthropic tool_result block at [{msg_idx}][{b_idx}] missing 'tool_use_id'"
                )
            if tool_use_id not in seen_tool_use_ids:
                # Mirrors the openai_messages orphan-tool-result check
                # and the gemini_contents FIFO check (PR #230 review).
                raise CatalogError(
                    f"Anthropic tool_result block at [{msg_idx}][{b_idx}] has "
                    f"tool_use_id={tool_use_id!r} that does not match any prior "
                    "assistant tool_use block"
                )
            text, content_for_meta = _stringify_tool_result_content(block.get("content"))
            meta = {
                **base_meta,
                "tool_use_id": tool_use_id,
                "is_error": bool(block.get("is_error", False)),
                # Preserve whether is_error was *present* in the original
                # block (vs. defaulted) so we can re-emit explicit False.
                "is_error_present": "is_error" in block,
                "content_payload": content_for_meta,
            }
            out.append(
                ContextItem(
                    id=f"{_PREFIX_TOOL_RESULT}{tool_use_id}",
                    kind=ItemKind.tool_result,
                    text=text,
                    metadata=meta,
                    parent_id=f"{_PREFIX_TOOL_USE}{tool_use_id}",
                )
            )
        else:
            raise CatalogError(
                f"Anthropic content block at [{msg_idx}][{b_idx}] has unsupported "
                f"type: {block_type!r}"
            )
    return out


def _stringify_tool_result_content(
    content: Any,  # noqa: ANN401 — content is opaque provider JSON
) -> tuple[str, Any]:
    """Reduce a tool_result.content payload to a string for ``ContextItem.text``.

    Returns a ``(text, original_content)`` tuple — the original is stashed
    in metadata so the inverse adapter can re-emit the exact same shape.
    """
    if content is None:
        return "", None
    if isinstance(content, str):
        return content, content
    if isinstance(content, list):
        text_parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text_parts.append(str(part.get("text", "")))
            else:
                text_parts.append(json.dumps(part, sort_keys=True))
        return "\n".join(text_parts), content
    # Numbers, bools — coerce to string and store the original for round-trip.
    return str(content), content


# --- Encoding (items → messages) ---


_block_index_sort_key = sort_key_by_meta_index("block_index")


def _item_to_block(item: ContextItem) -> dict[str, Any]:
    """Convert a single ContextItem back into an Anthropic content block.

    Starts from the original decoded block (so unknown provider fields like
    ``cache_control`` survive the round-trip) and overlays the canonical
    decoded fields. Falls back to a constructed block when no original is
    available (hand-constructed items).
    """
    meta = item.metadata or {}
    block_type = meta.get("block_type")
    original_block = meta.get("original_block")
    # When original_block is present, start from a copy and overlay the
    # canonical decoded fields. This preserves unknown / non-decoded keys
    # (e.g. cache_control, citations) verbatim.
    block: dict[str, Any] = dict(original_block) if isinstance(original_block, dict) else {}

    if block_type == "text":
        block["type"] = "text"
        block["text"] = item.text
        return block
    if block_type == "tool_use":
        tool_use_id = meta.get("tool_use_id")
        if not tool_use_id:
            raise CatalogError(f"tool_call item {item.id!r} missing 'tool_use_id' metadata")
        block["type"] = "tool_use"
        block["id"] = tool_use_id
        block["name"] = meta.get("function_name", "")
        block["input"] = meta.get("input", {})
        return block
    if block_type == "tool_result":
        tool_use_id = meta.get("tool_use_id")
        if not tool_use_id:
            raise CatalogError(f"tool_result item {item.id!r} missing 'tool_use_id' metadata")
        block["type"] = "tool_result"
        block["tool_use_id"] = tool_use_id
        # Preserve the original content shape: string vs list vs missing.
        content_payload = meta.get("content_payload")
        if content_payload is not None:
            block["content"] = content_payload
        elif "content" in block:
            # Original block had no content; ensure we don't accidentally
            # keep a content key from the original_block snapshot.
            del block["content"]
        # is_error: re-emit only if it was present in the original block.
        # Preserves explicit `is_error: False` and omits when absent.
        if meta.get("is_error_present"):
            block["is_error"] = bool(meta.get("is_error", False))
        else:
            block.pop("is_error", None)
        return block
    # Fallback for items that were assembled without the round-trip metadata
    # (e.g., constructed by hand). Emit as a plain text block carrying the
    # item's text so the message remains valid Anthropic input.
    return {"type": "text", "text": item.text}

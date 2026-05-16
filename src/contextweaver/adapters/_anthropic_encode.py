"""Internal: ContextItem → Anthropic Messages encoders.

Implementation detail of :mod:`contextweaver.adapters.anthropic_messages`;
not part of the public API. Importing directly is unsupported. Lives in
a separate module so the public adapter file stays within the repo's
≤300-line module guideline (see ``AGENTS.md``).
"""

from __future__ import annotations

from typing import Any

from contextweaver.adapters._messages_common import sort_key_by_meta_index
from contextweaver.exceptions import CatalogError
from contextweaver.types import ContextItem

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

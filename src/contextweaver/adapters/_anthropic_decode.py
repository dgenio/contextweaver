"""Internal: Anthropic Messages → ContextItem decoders.

Implementation detail of :mod:`contextweaver.adapters.anthropic_messages`;
not part of the public API. Importing directly is unsupported. Lives in
a separate module so the public adapter file stays within the repo's
≤300-line module guideline (see ``AGENTS.md``).
"""

from __future__ import annotations

import json
from typing import Any

from contextweaver.adapters._messages_common import expect_dict, json_args_dumps
from contextweaver.exceptions import CatalogError
from contextweaver.types import ContextItem, ItemKind

_PREFIX_USER = "anthropic:user:"
_PREFIX_ASSISTANT = "anthropic:assistant:"
_PREFIX_TOOL_USE = "anthropic:tool_use:"
_PREFIX_TOOL_RESULT = "anthropic:tool_result:"


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

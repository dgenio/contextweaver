"""Internal: ContextItem → OpenAI Chat Completions encoders.

Implementation detail of :mod:`contextweaver.adapters.openai_messages`;
not part of the public API. Importing directly is unsupported. Lives in
a separate module so the public adapter file stays within the repo's
≤300-line module guideline (see ``AGENTS.md``).
"""

from __future__ import annotations

from typing import Any

from contextweaver.adapters._messages_common import strip_prefix
from contextweaver.adapters._openai_decode import _PREFIX_TOOL_CALL
from contextweaver.exceptions import CatalogError
from contextweaver.types import ContextItem, ItemKind


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


def _restore_assistant_content(item: ContextItem, meta: dict[str, Any]) -> Any:  # noqa: ANN401 — provider-shaped JSON
    """Reconstruct assistant ``content``: None / list / string (PR #230)."""
    if meta.get("content_is_null"):
        return None
    original = meta.get("original_content")
    if isinstance(original, list):
        return original
    return item.text

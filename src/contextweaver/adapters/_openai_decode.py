"""Internal: OpenAI Chat Completions → ContextItem decoders.

Implementation detail of :mod:`contextweaver.adapters.openai_messages`;
not part of the public API. Importing directly is unsupported. Lives in
a separate module so the public adapter file stays within the repo's
≤300-line module guideline (see ``AGENTS.md``).
"""

from __future__ import annotations

import json
from typing import Any

from contextweaver.adapters._messages_common import expect_dict
from contextweaver.exceptions import CatalogError
from contextweaver.types import ContextItem, ItemKind

# ID prefix carries enough information to round-trip the OpenAI message:
# - "openai:tool_call:<tool_call_id>"   → the assistant's tool-call entry
# - "openai:tool_result:<tool_call_id>" → the matching role="tool" response
# - "openai:{user,assistant,system}:<idx>" → turn entries
_PREFIX_TOOL_CALL = "openai:tool_call:"
_PREFIX_TOOL_RESULT = "openai:tool_result:"
_PREFIX_USER = "openai:user:"
_PREFIX_ASSISTANT = "openai:assistant:"
_PREFIX_SYSTEM = "openai:system:"


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

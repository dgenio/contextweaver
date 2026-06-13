"""Run-item ingestion helpers for the OpenAI Agents SDK adapter (issue #501).

Private module backing :func:`contextweaver.adapters.openai_agents.from_openai_agents_run`;
kept separate so ``openai_agents.py`` stays within the ≤300-line module ceiling.
Not public API.

Converts an OpenAI Agents SDK run's items (``RunResult.new_items`` / the
session item list) into :class:`~contextweaver.types.ContextItem`s, preserving
tool-call → tool-output parentage so dependency closure includes the call when
its result is selected.  The decoder is dict-shaped so it runs without the
``openai-agents`` SDK installed; live ``RunItem`` objects are coerced to dicts
via ``to_dict`` / ``model_dump`` / attribute access first.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from contextweaver.adapters._messages_common import (
    expect_dict,
    expect_list,
    ingest_into_manager,
    json_args_dumps,
)
from contextweaver.exceptions import CatalogError
from contextweaver.types import ContextItem, ItemKind

if TYPE_CHECKING:
    from contextweaver.context.manager import ContextManager

logger = logging.getLogger("contextweaver.adapters")

_ID_PREFIX = "openai_agents"

# The SDK and its serialised shapes spell the same item families several ways;
# normalise them to one discriminator each.
_MESSAGE_TYPES = frozenset({"message_output", "message_output_item", "message"})
_TOOL_CALL_TYPES = frozenset({"tool_call", "tool_call_item"})
_TOOL_OUTPUT_TYPES = frozenset({"tool_call_output", "tool_call_output_item", "tool_output"})
_HANDOFF_TYPES = frozenset({"handoff", "handoff_call_item", "handoff_output_item"})
_REASONING_TYPES = frozenset({"reasoning", "reasoning_item"})
# Known SDK item types that carry no conversational text worth ingesting
# (approval prompts, MCP catalog/approval control items, history compaction
# markers).  They are skipped rather than raised on so ingestion stays robust
# across SDK versions; genuinely unknown types still raise (a likely caller
# mistake or a new family we should map deliberately).
_SKIP_TYPES = frozenset(
    {
        "tool_approval_item",
        "mcp_list_tools_item",
        "mcp_approval_request_item",
        "mcp_approval_response_item",
        "compaction",
        "compaction_item",
    }
)


def _item_to_dict(item: object) -> dict[str, Any]:
    """Coerce a live ``RunItem`` (or already-dict) into a plain dict."""
    if isinstance(item, dict):
        return dict(item)
    for fn_name in ("to_dict", "model_dump"):
        fn = getattr(item, fn_name, None)
        if callable(fn):
            try:
                dumped = fn()
            except Exception as exc:  # pragma: no cover - defensive
                raise CatalogError(
                    f"OpenAI Agents run item {item!r}.{fn_name}() raised: {exc}"
                ) from exc
            if isinstance(dumped, dict):
                return dumped
    try:
        return dict(vars(item))
    except TypeError as exc:
        raise CatalogError(
            f"OpenAI Agents run item {item!r} is neither a dict nor has dumpable attributes."
        ) from exc


def _collect_run_items(run_or_items: object) -> list[Any]:
    """Walk a run/result object (or list) to a flat item list."""
    if isinstance(run_or_items, list):
        return list(run_or_items)
    for attr in ("new_items", "items"):
        candidate = getattr(run_or_items, attr, None)
        if isinstance(candidate, list):
            return list(candidate)
    raise CatalogError(
        "from_openai_agents_run could not locate a 'new_items' or 'items' iterable on the input."
    )


def _normalise_type(item: dict[str, Any]) -> str:
    raw = item.get("type") or item.get("item_type") or "message_output"
    return str(raw)


def _text_of(item: dict[str, Any]) -> str:
    """Best-effort extraction of human-readable text from a run item.

    Checks the common flat string fields first, then digs into a ``content``
    block list (the message shape the SDK emits). Returns ``""`` when no
    readable text is present.
    """
    for key in ("text", "output_text", "content"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    content = item.get("content")
    if isinstance(content, list):
        parts = [
            block.get("text") or block.get("output_text")
            for block in content
            if isinstance(block, dict)
        ]
        joined = "".join(part for part in parts if isinstance(part, str) and part)
        if joined:
            return joined
    return ""


def _payload_dump(data: dict[str, Any]) -> str:
    """Deterministic JSON dump used when an item carries no readable text.

    ``default=str`` keeps it total: a non-JSON-native value (e.g. a leftover
    SDK object from ``vars()``) is stringified rather than raising, so
    ingestion never fails on an otherwise-usable run.
    """
    return json.dumps(data, sort_keys=True, default=str)


def decode_run_items(
    run_or_items: object,
    into: ContextManager | None = None,
) -> list[ContextItem]:
    """Convert an OpenAI Agents SDK run / item list into :class:`ContextItem`s.

    Accepts either a run/result object exposing ``new_items`` / ``items`` or a
    plain list of run-item dicts/objects.  See
    :func:`contextweaver.adapters.openai_agents.from_openai_agents_run` for the
    mapping rules and the public docstring.
    """
    raw_items = _collect_run_items(run_or_items)
    expect_list(raw_items, fn_name="from_openai_agents_run")

    items: list[ContextItem] = []
    for idx, raw in enumerate(raw_items):
        data = _item_to_dict(raw)
        expect_dict(data, label=f"OpenAI Agents run item at index {idx}")
        kind = _normalise_type(data)
        if kind in _TOOL_CALL_TYPES:
            items.append(_decode_tool_call(idx, data))
        elif kind in _TOOL_OUTPUT_TYPES:
            items.append(_decode_tool_output(idx, data))
        elif kind in _HANDOFF_TYPES:
            items.append(_decode_handoff(idx, data))
        elif kind in _REASONING_TYPES:
            reasoning = _maybe_reasoning(idx, data)
            if reasoning is not None:
                items.append(reasoning)
        elif kind in _MESSAGE_TYPES:
            items.append(_decode_message(idx, data))
        elif kind in _SKIP_TYPES:
            logger.debug("from_openai_agents_run: skipping control item type %r", kind)
        else:
            raise CatalogError(f"OpenAI Agents run item at index {idx} has unknown type {kind!r}.")

    ingest_into_manager(items, into)
    return items


def _call_id_of(data: dict[str, Any], idx: int) -> str:
    call_id = data.get("call_id") or data.get("id") or data.get("tool_call_id")
    if not isinstance(call_id, str) or not call_id:
        call_id = f"{_ID_PREFIX}-call-{idx}"
    return call_id


def _decode_message(idx: int, data: dict[str, Any]) -> ContextItem:
    # Fall back to a deterministic JSON dump when the item carries no readable
    # text, so the ingested turn never has an empty body.
    text = _text_of(data) or _payload_dump(data)
    return ContextItem(
        id=f"{_ID_PREFIX}:message:{idx}",
        kind=ItemKind.agent_msg,
        text=text,
        metadata={"item_index": idx, "provider": _ID_PREFIX},
    )


def _decode_tool_call(idx: int, data: dict[str, Any]) -> ContextItem:
    call_id = _call_id_of(data, idx)
    tool_name = data.get("name") or data.get("tool_name") or ""
    args_payload = data.get("arguments", data.get("args", "{}"))
    args_text = (
        args_payload
        if isinstance(args_payload, str)
        else json_args_dumps(args_payload, label=f"openai_agents tool_call {call_id!r}")
    )
    return ContextItem(
        id=f"{_ID_PREFIX}:tool_call:{call_id}",
        kind=ItemKind.tool_call,
        text=args_text,
        metadata={
            "item_index": idx,
            "provider": _ID_PREFIX,
            "tool_name": tool_name,
            "tool_call_id": call_id,
        },
    )


def _decode_tool_output(idx: int, data: dict[str, Any]) -> ContextItem:
    call_id = _call_id_of(data, idx)
    output = data.get("output", data.get("content", ""))
    text = (
        output
        if isinstance(output, str)
        else json_args_dumps(output, label=f"openai_agents tool_output {call_id!r}")
    )
    return ContextItem(
        id=f"{_ID_PREFIX}:tool_result:{call_id}",
        kind=ItemKind.tool_result,
        text=text,
        parent_id=f"{_ID_PREFIX}:tool_call:{call_id}",
        metadata={
            "item_index": idx,
            "provider": _ID_PREFIX,
            "tool_call_id": call_id,
        },
    )


def _decode_handoff(idx: int, data: dict[str, Any]) -> ContextItem:
    source = data.get("source") or data.get("source_agent") or ""
    target = data.get("target") or data.get("target_agent") or ""
    text = data.get("content")
    if not isinstance(text, str) or not text:
        text = f"Handoff: {source or '?'} -> {target or '?'}"
    return ContextItem(
        id=f"{_ID_PREFIX}:handoff:{idx}",
        kind=ItemKind.agent_msg,
        text=text,
        metadata={
            "item_index": idx,
            "provider": _ID_PREFIX,
            "handoff": True,
            "source_agent": source,
            "target_agent": target,
        },
    )


def _maybe_reasoning(idx: int, data: dict[str, Any]) -> ContextItem | None:
    text = _text_of(data)
    if not text.strip():
        return None
    return ContextItem(
        id=f"{_ID_PREFIX}:reasoning:{idx}",
        kind=ItemKind.agent_msg,
        text=text,
        metadata={"item_index": idx, "provider": _ID_PREFIX, "reasoning": True},
    )

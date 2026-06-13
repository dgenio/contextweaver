"""Session ingestion helpers for the Google ADK adapter (issue #547).

Private module backing :func:`contextweaver.adapters.google_adk.from_google_adk_session`;
kept separate so ``google_adk.py`` stays within the ≤300-line module ceiling.
Not public API.

Converts a Google ADK ``Session``'s events (``Session.events`` / an event list)
into :class:`~contextweaver.types.ContextItem`s.  ADK events carry a
``Content`` with ``parts``; a part is text, a ``function_call``, or a
``function_response``.  Function-response parts link back to their originating
``function_call`` via ``parent_id`` so dependency closure includes the call
when its result is selected.  The decoder is dict-shaped so it runs without the
``google-adk`` SDK installed; live objects are coerced via
``to_dict`` / ``model_dump`` / attribute access first.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from contextweaver.adapters._messages_common import (
    expect_list,
    ingest_into_manager,
    json_args_dumps,
)
from contextweaver.exceptions import CatalogError
from contextweaver.types import ContextItem, ItemKind

if TYPE_CHECKING:
    from contextweaver.context.manager import ContextManager

logger = logging.getLogger("contextweaver.adapters")

_ID_PREFIX = "google_adk"


def _to_dict(obj: object) -> Any:  # noqa: ANN401 — opaque SDK object
    """Best-effort coercion of an ADK object (Event / Content / Part) to a dict."""
    if isinstance(obj, dict):
        return obj
    for fn_name in ("model_dump", "to_dict"):
        fn = getattr(obj, fn_name, None)
        if callable(fn):
            try:
                dumped = fn()
            except Exception as exc:  # pragma: no cover - defensive
                raise CatalogError(f"Google ADK object {obj!r}.{fn_name}() raised: {exc}") from exc
            if isinstance(dumped, dict):
                return dumped
    return obj


def _collect_events(session_or_events: object) -> list[Any]:
    if isinstance(session_or_events, list):
        return list(session_or_events)
    events = getattr(session_or_events, "events", None)
    if isinstance(events, list):
        return list(events)
    raise CatalogError(
        "from_google_adk_session could not locate an 'events' iterable on the input."
    )


def _event_parts(event: dict[str, Any]) -> tuple[str, list[Any]]:
    """Return ``(role, parts)`` for an event, normalising author/content shapes."""
    content = _to_dict(event.get("content")) if event.get("content") is not None else {}
    if not isinstance(content, dict):
        content = {}
    role = content.get("role") or event.get("author") or ""
    parts = content.get("parts")
    return (str(role), parts if isinstance(parts, list) else [])


def decode_session(
    session_or_events: object,
    into: ContextManager | None = None,
) -> list[ContextItem]:
    """Convert a Google ADK session / event list into :class:`ContextItem`s.

    See :func:`contextweaver.adapters.google_adk.from_google_adk_session` for
    the public docstring and mapping rules.
    """
    raw_events = _collect_events(session_or_events)
    expect_list(raw_events, fn_name="from_google_adk_session")

    items: list[ContextItem] = []
    for idx, raw_event in enumerate(raw_events):
        event = _to_dict(raw_event)
        if not isinstance(event, dict):
            raise CatalogError(f"Google ADK event at index {idx} is not a dict-like object.")
        role, parts = _event_parts(event)
        for part_idx, raw_part in enumerate(parts):
            part = _to_dict(raw_part)
            if not isinstance(part, dict):
                continue
            item = _decode_part(idx, part_idx, role, part)
            if item is not None:
                items.append(item)

    ingest_into_manager(items, into)
    return items


def _is_user(role: str) -> bool:
    return role.lower() == "user"


def _decode_part(
    idx: int,
    part_idx: int,
    role: str,
    part: dict[str, Any],
) -> ContextItem | None:
    """Decode one ADK content part into a :class:`ContextItem` (or ``None``)."""
    call = part.get("function_call")
    if isinstance(call, dict):
        return _decode_function_call(idx, part_idx, call)
    response = part.get("function_response")
    if isinstance(response, dict):
        return _decode_function_response(idx, part_idx, response)
    text = part.get("text")
    if isinstance(text, str) and text.strip():
        kind = ItemKind.user_turn if _is_user(role) else ItemKind.agent_msg
        return ContextItem(
            id=f"{_ID_PREFIX}:text:{idx}:{part_idx}",
            kind=kind,
            text=text,
            metadata={"event_index": idx, "provider": _ID_PREFIX, "role": role},
        )
    return None


def _call_id_of(payload: dict[str, Any], idx: int, part_idx: int) -> str:
    call_id = payload.get("id") or payload.get("call_id")
    if not isinstance(call_id, str) or not call_id:
        call_id = f"{_ID_PREFIX}-call-{idx}-{part_idx}"
    return call_id


def _decode_function_call(idx: int, part_idx: int, call: dict[str, Any]) -> ContextItem:
    call_id = _call_id_of(call, idx, part_idx)
    tool_name = call.get("name") or ""
    args_payload = call.get("args", call.get("arguments", {}))
    args_text = (
        args_payload
        if isinstance(args_payload, str)
        else json_args_dumps(args_payload, label=f"google_adk function_call {call_id!r}")
    )
    return ContextItem(
        id=f"{_ID_PREFIX}:tool_call:{call_id}",
        kind=ItemKind.tool_call,
        text=args_text,
        metadata={
            "event_index": idx,
            "provider": _ID_PREFIX,
            "tool_name": tool_name,
            "tool_call_id": call_id,
        },
    )


def _decode_function_response(idx: int, part_idx: int, response: dict[str, Any]) -> ContextItem:
    call_id = _call_id_of(response, idx, part_idx)
    payload = response.get("response", response.get("output", ""))
    text = (
        payload
        if isinstance(payload, str)
        else json_args_dumps(payload, label=f"google_adk function_response {call_id!r}")
    )
    return ContextItem(
        id=f"{_ID_PREFIX}:tool_result:{call_id}",
        kind=ItemKind.tool_result,
        text=text,
        parent_id=f"{_ID_PREFIX}:tool_call:{call_id}",
        metadata={
            "event_index": idx,
            "provider": _ID_PREFIX,
            "tool_name": response.get("name", ""),
            "tool_call_id": call_id,
        },
    )

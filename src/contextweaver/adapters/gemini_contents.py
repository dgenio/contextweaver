"""Google Gemini ``contents``-array adapter for contextweaver.

Bridges the Google Gemini API ``contents[]`` schema and contextweaver's
:class:`~contextweaver.types.ContextItem` event log:

.. code-block:: python

    from contextweaver.context.manager import ContextManager
    from contextweaver.adapters.gemini_contents import from_gemini_contents

    mgr = ContextManager()
    from_gemini_contents(contents, into=mgr)

The adapter is a pure stateless converter — no provider SDK is imported
at module load time (per the ``adapters/`` path convention). Operate on
plain ``dict``s following the documented Gemini JSON schema.

Gemini's native shape differs from OpenAI's and Anthropic's:

- Top-level is a list of ``Content`` objects (``contents[]``), each with
  ``role`` and ``parts[]``.
- Roles are ``"user"`` and ``"model"`` (not ``"assistant"``). Tool
  responses use the synthetic role ``"function"`` per the SDK.
- Each ``Part`` is one of: ``text``, ``functionCall``, ``functionResponse``,
  ``inlineData`` (skipped), ``fileData`` (skipped).
- **There is no native ID** on ``functionCall`` — we synthesise a
  deterministic one as ``"<name>:<msg_index>:<part_index>"`` so the
  inverse adapter is reproducible.

Mapping rules:

- ``role="user"`` ``text`` part → :data:`ItemKind.user_turn`
- ``role="model"`` ``text`` part → :data:`ItemKind.agent_msg`
- ``role="model"`` ``functionCall`` part → :data:`ItemKind.tool_call`
- ``role="function"`` ``functionResponse`` part → :data:`ItemKind.tool_result`
  with ``parent_id`` set to the matching ``functionCall``'s synthetic id.

Issue #222 (closes #194 together with the OpenAI slice #219 and the
Anthropic adapter).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from contextweaver.exceptions import CatalogError
from contextweaver.types import ContextItem, ItemKind

if TYPE_CHECKING:
    from contextweaver.context.manager import ContextManager

logger = logging.getLogger("contextweaver.adapters")

_PREFIX_USER = "gemini:user:"
_PREFIX_MODEL = "gemini:model:"
_PREFIX_FUNCTION_CALL = "gemini:functionCall:"
_PREFIX_FUNCTION_RESPONSE = "gemini:functionResponse:"


# ---------------------------------------------------------------------------
# Public: from_gemini_contents
# ---------------------------------------------------------------------------


def from_gemini_contents(
    contents: list[dict[str, Any]],
    into: ContextManager | None = None,
) -> list[ContextItem]:
    """Convert a Gemini ``contents`` array into ContextItems.

    Args:
        contents: A list of Gemini ``Content`` dicts. Each must have
            ``role`` and ``parts`` (a list of part dicts).
        into: Optional :class:`~contextweaver.context.manager.ContextManager`.
            When provided, each returned item is appended via
            :meth:`ContextManager.ingest` in order.

    Returns:
        A list of :class:`ContextItem` in input order. Multi-part contents
        expand into multiple items; the part index is stored in
        ``metadata["part_index"]`` so the inverse adapter can rebuild the
        original ``parts`` order.

    Raises:
        CatalogError: On unknown role, unknown part type, malformed input,
            or a ``functionResponse`` whose ``name`` does not match a prior
            ``functionCall``.
    """
    if not isinstance(contents, list):
        raise CatalogError(f"from_gemini_contents expects a list, got {type(contents).__name__}")

    items: list[ContextItem] = []
    # name → list of synthesised IDs from prior functionCalls (FIFO). When a
    # functionResponse arrives with role="function", it pops the oldest unmatched
    # functionCall with the same `name` to set parent_id. This matches Gemini's
    # convention of pairing function calls and responses by name in order.
    pending_calls: dict[str, list[str]] = {}

    for msg_idx, content in enumerate(contents):
        if not isinstance(content, dict):
            raise CatalogError(
                f"Gemini content at index {msg_idx} is not a dict: {type(content).__name__}"
            )
        role = content.get("role")
        if role not in ("user", "model", "function"):
            raise CatalogError(f"Gemini content at index {msg_idx} has unknown role: {role!r}")
        parts = content.get("parts")
        if not isinstance(parts, list):
            raise CatalogError(
                f"Gemini content at index {msg_idx} has non-list parts: {type(parts).__name__}"
            )
        for p_idx, part in enumerate(parts):
            if not isinstance(part, dict):
                raise CatalogError(
                    f"Gemini part at [{msg_idx}][{p_idx}] is not a dict: {type(part).__name__}"
                )
            items.append(_part_to_item(part, role, msg_idx, p_idx, pending_calls))

    if into is not None:
        for item in items:
            into.ingest(item)

    logger.debug("from_gemini_contents: contents_in=%d, items_out=%d", len(contents), len(items))
    return items


# ---------------------------------------------------------------------------
# Public: to_gemini_contents
# ---------------------------------------------------------------------------


def to_gemini_contents(items: list[ContextItem]) -> list[dict[str, Any]]:
    """Inverse of :func:`from_gemini_contents`.

    Re-groups items by ``metadata["msg_index"]`` and emits their parts in
    original ``part_index`` order. Functions calls and responses are placed
    under the original role of their parent content.

    Args:
        items: Items produced by :func:`from_gemini_contents`.

    Returns:
        A list of Gemini content dicts.

    Raises:
        CatalogError: If items lack the required round-trip metadata.
    """
    groups: dict[int, list[ContextItem]] = {}
    for item in items:
        meta = item.metadata or {}
        msg_idx = meta.get("msg_index")
        if msg_idx is None:
            raise CatalogError(
                f"ContextItem {item.id!r} missing 'msg_index' metadata; cannot "
                "round-trip back to Gemini contents"
            )
        groups.setdefault(int(msg_idx), []).append(item)

    out: list[dict[str, Any]] = []
    for msg_idx in sorted(groups):
        group = sorted(groups[msg_idx], key=_part_index_sort_key)
        first_meta = group[0].metadata or {}
        role = first_meta.get("role")
        if role not in ("user", "model", "function"):
            raise CatalogError(f"ContextItem group msg_index={msg_idx} has invalid role={role!r}")
        parts = [_item_to_part(item) for item in group]
        out.append({"role": role, "parts": parts})
    return out


# ---------------------------------------------------------------------------
# Decoders
# ---------------------------------------------------------------------------


def _part_to_item(
    part: dict[str, Any],
    role: str,
    msg_idx: int,
    p_idx: int,
    pending_calls: dict[str, list[str]],
) -> ContextItem:
    base_meta: dict[str, Any] = {
        "role": role,
        "msg_index": msg_idx,
        "part_index": p_idx,
    }
    if "text" in part:
        base_meta["part_type"] = "text"
        text = str(part.get("text", ""))
        if role == "user":
            kind = ItemKind.user_turn
            prefix = _PREFIX_USER
        elif role == "model":
            kind = ItemKind.agent_msg
            prefix = _PREFIX_MODEL
        else:
            # role="function" with a text part is exotic; preserve as agent_msg
            # so the round-trip is lossless.
            kind = ItemKind.agent_msg
            prefix = _PREFIX_MODEL
        return ContextItem(
            id=f"{prefix}{msg_idx}:{p_idx}",
            kind=kind,
            text=text,
            metadata=base_meta,
        )
    if "functionCall" in part:
        if role != "model":
            raise CatalogError(
                f"Gemini functionCall at [{msg_idx}][{p_idx}] must be on a "
                f"'model' content, got role={role!r}"
            )
        fc = part["functionCall"]
        if not isinstance(fc, dict):
            raise CatalogError(f"Gemini functionCall at [{msg_idx}][{p_idx}] is not a dict")
        name = fc.get("name")
        if not isinstance(name, str) or not name:
            raise CatalogError(f"Gemini functionCall at [{msg_idx}][{p_idx}] missing 'name'")
        # Synthesise a deterministic ID. Gemini doesn't ship one natively;
        # name + position is reproducible and survives the round-trip.
        synthesised_id = f"{name}:{msg_idx}:{p_idx}"
        pending_calls.setdefault(name, []).append(synthesised_id)
        args = fc.get("args", {})
        try:
            args_str = json.dumps(args, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise CatalogError(
                f"Gemini functionCall.args at [{msg_idx}][{p_idx}] is not JSON-serialisable: {exc}"
            ) from exc
        meta = {
            **base_meta,
            "part_type": "functionCall",
            "function_name": name,
            "function_call_id": synthesised_id,
            "args": args,
        }
        return ContextItem(
            id=f"{_PREFIX_FUNCTION_CALL}{synthesised_id}",
            kind=ItemKind.tool_call,
            text=args_str,
            metadata=meta,
        )
    if "functionResponse" in part:
        if role != "function":
            raise CatalogError(
                f"Gemini functionResponse at [{msg_idx}][{p_idx}] must be on a "
                f"'function' content, got role={role!r}"
            )
        fr = part["functionResponse"]
        if not isinstance(fr, dict):
            raise CatalogError(f"Gemini functionResponse at [{msg_idx}][{p_idx}] is not a dict")
        name = fr.get("name")
        if not isinstance(name, str) or not name:
            raise CatalogError(f"Gemini functionResponse at [{msg_idx}][{p_idx}] missing 'name'")
        queue = pending_calls.get(name) or []
        if not queue:
            raise CatalogError(
                f"Gemini functionResponse at [{msg_idx}][{p_idx}] for name={name!r} "
                "has no matching prior functionCall"
            )
        # FIFO: match the oldest unanswered call with this name.
        matched_call_id = queue.pop(0)
        if not queue:
            pending_calls.pop(name, None)
        response = fr.get("response", {})
        try:
            response_str = json.dumps(response, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise CatalogError(
                f"Gemini functionResponse.response at [{msg_idx}][{p_idx}] is "
                f"not JSON-serialisable: {exc}"
            ) from exc
        meta = {
            **base_meta,
            "part_type": "functionResponse",
            "function_name": name,
            "function_call_id": matched_call_id,
            "response": response,
        }
        return ContextItem(
            id=f"{_PREFIX_FUNCTION_RESPONSE}{matched_call_id}",
            kind=ItemKind.tool_result,
            text=response_str,
            metadata=meta,
            parent_id=f"{_PREFIX_FUNCTION_CALL}{matched_call_id}",
        )
    raise CatalogError(
        f"Gemini part at [{msg_idx}][{p_idx}] has no recognised content "
        f"(text / functionCall / functionResponse); got keys {list(part.keys())}"
    )


# ---------------------------------------------------------------------------
# Encoders
# ---------------------------------------------------------------------------


def _part_index_sort_key(item: ContextItem) -> int:
    meta = item.metadata or {}
    part_idx = meta.get("part_index", 0)
    try:
        return int(part_idx)
    except (TypeError, ValueError):
        return 0


def _item_to_part(item: ContextItem) -> dict[str, Any]:
    meta = item.metadata or {}
    part_type = meta.get("part_type")
    if part_type == "text":
        return {"text": item.text}
    if part_type == "functionCall":
        name = meta.get("function_name")
        if not name:
            raise CatalogError(f"tool_call item {item.id!r} missing 'function_name' metadata")
        return {"functionCall": {"name": name, "args": meta.get("args", {})}}
    if part_type == "functionResponse":
        name = meta.get("function_name")
        if not name:
            raise CatalogError(f"tool_result item {item.id!r} missing 'function_name' metadata")
        return {"functionResponse": {"name": name, "response": meta.get("response", {})}}
    # Fallback: round-trip what we can as a text part so the message stays
    # valid Gemini input.
    return {"text": item.text}

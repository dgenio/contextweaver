"""Shared helpers for provider-message ingestion adapters.

Used by :mod:`.openai_messages`, :mod:`.anthropic_messages`, and
:mod:`.gemini_contents` to keep per-provider modules focused on the
provider-specific decoding rules. These helpers cover the patterns that
recur identically across all three adapters: top-level type validation,
``into=ContextManager.ingest()`` plumbing, JSON-args serialisation,
``metadata["msg_index"]`` grouping, and prefix-stripping.

This is a private module — its API is not exported from
``contextweaver.adapters`` and is not part of the public contract.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, NoReturn

from contextweaver.exceptions import CatalogError
from contextweaver.types import ContextItem

if TYPE_CHECKING:
    from contextweaver.context.manager import ContextManager

logger = logging.getLogger("contextweaver.adapters")


def expect_list(value: Any, *, fn_name: str) -> None:  # noqa: ANN401 — provider JSON
    """Raise :class:`CatalogError` when *value* is not a ``list``.

    Args:
        value: The opaque input to validate.
        fn_name: Caller name, used in the error message.

    Raises:
        CatalogError: If *value* is not a ``list``.
    """
    if not isinstance(value, list):
        raise CatalogError(f"{fn_name} expects a list, got {type(value).__name__}")


def expect_dict(value: Any, *, label: str) -> None:  # noqa: ANN401 — provider JSON
    """Raise :class:`CatalogError` when *value* is not a ``dict``.

    Args:
        value: The opaque input to validate.
        label: Position label (e.g. ``"OpenAI message at index 3"``).

    Raises:
        CatalogError: If *value* is not a ``dict``.
    """
    if not isinstance(value, dict):
        raise CatalogError(f"{label} is not a dict: {type(value).__name__}")


def ingest_into_manager(items: list[ContextItem], into: ContextManager | None) -> None:
    """Append each item to *into*'s event log when *into* is provided.

    No-op when *into* is ``None``. Items are appended in list order.
    """
    if into is None:
        return
    for item in items:
        into.ingest(item)


def json_args_dumps(payload: Any, *, label: str) -> str:  # noqa: ANN401 — provider JSON
    """JSON-encode tool-call args/input with deterministic key ordering.

    Args:
        payload: The arbitrary tool-call argument payload.
        label: Position label for the error message.

    Returns:
        A canonical JSON string (sorted keys).

    Raises:
        CatalogError: When *payload* is not JSON-serialisable.
    """
    try:
        return json.dumps(payload, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise CatalogError(f"{label} is not JSON-serialisable: {exc}") from exc


def group_items_by_msg_index(
    items: list[ContextItem], *, target_label: str
) -> dict[int, list[ContextItem]]:
    """Group items by ``metadata["msg_index"]``.

    Used by the Anthropic and Gemini encoders, both of which need to
    rebuild multi-block / multi-part messages from per-block items.

    Args:
        items: Items produced by a sibling ``from_*`` decoder.
        target_label: Provider label for the error message
            (e.g. ``"Anthropic messages"``, ``"Gemini contents"``).

    Returns:
        A dict mapping message index to its constituent items, in
        insertion order within each group.

    Raises:
        CatalogError: If any item is missing the ``msg_index`` metadata
            entry the inverse round-trip requires.
    """
    groups: dict[int, list[ContextItem]] = {}
    for item in items:
        meta = item.metadata or {}
        msg_idx = meta.get("msg_index")
        if msg_idx is None:
            raise CatalogError(
                f"ContextItem {item.id!r} missing 'msg_index' metadata; cannot "
                f"round-trip back to {target_label}"
            )
        groups.setdefault(int(msg_idx), []).append(item)
    return groups


def sort_key_by_meta_index(meta_key: str) -> Callable[[ContextItem], int]:
    """Build a sort key function reading ``metadata[meta_key]`` as ``int``.

    Returns ``0`` when the key is missing or not convertible to ``int``.
    Used by both ``_block_index_sort_key`` (Anthropic) and
    ``_part_index_sort_key`` (Gemini) to keep multi-block / multi-part
    output in original input order.
    """

    def key_fn(item: ContextItem) -> int:
        meta = item.metadata or {}
        val = meta.get(meta_key, 0)
        try:
            return int(val)
        except (TypeError, ValueError):
            return 0

    return key_fn


def is_blank_content(value: Any) -> bool:  # noqa: ANN401 — provider-shaped JSON
    """True when *value* carries no renderable content.

    Treats ``None``, an empty / whitespace-only string, and an empty list or
    tuple as blank. Non-empty collections and any other type are considered
    non-blank. Used by the ``to_*`` encoders to detect message turns that
    would serialise to empty content (which provider chat APIs reject).
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple)):
        return len(value) == 0
    return False


def content_blocks_are_empty(text_values: list[str | None]) -> bool:
    """True when a message's content blocks carry no renderable content.

    *text_values* holds one entry per content block: the block's text when it
    is a text block, or ``None`` when it is a non-text block (tool call / tool
    result / function call — inherently non-empty). A message is empty when it
    has no blocks at all, or every block is a blank text block. Shared by the
    Anthropic (``blocks``) and Gemini (``parts``) encoders.
    """
    if not text_values:
        return True
    return all(value is not None and is_blank_content(value) for value in text_values)


def raise_empty_message_content(*, provider: str, locator: str, role: str) -> NoReturn:
    """Raise :class:`CatalogError` for a message that would serialise empty.

    Provider chat APIs reject messages whose content is empty (e.g. Anthropic
    returns ``400 ... messages: ... must have non-empty content``). The
    encoders fail fast here so the misuse surfaces at conversion time with an
    actionable locator, rather than as an opaque HTTP 400 from the provider.

    Args:
        provider: Human-readable provider name (e.g. ``"Anthropic"``).
        locator: Position hint for the offending message (e.g.
            ``"at msg_index=3"`` or ``"for item 'x'"``).
        role: The message's role, included for context.

    Raises:
        CatalogError: Always.
    """
    raise CatalogError(
        f"{provider} message {locator} (role={role!r}) would serialise to empty "
        "content, which the provider API rejects (e.g. '400 ... messages: ... must "
        "have non-empty content'). Remove the empty turn before conversion, or give "
        "it text."
    )


def strip_prefix(value: str, prefix: str) -> str:
    """Strip *prefix* from *value*; return an empty string if it doesn't match.

    Used by adapters that round-trip provider IDs through
    ``ContextItem.id`` to recover the original ID when the
    metadata-stored copy is missing.
    """
    return value[len(prefix) :] if value.startswith(prefix) else ""

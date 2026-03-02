"""Shared helpers for adapter JSONL session loaders."""

from __future__ import annotations

import json
from pathlib import Path

from contextweaver.exceptions import CatalogError
from contextweaver.types import ContextItem, ItemKind


def _load_session_jsonl(
    path: str | Path,
    *,
    default_kind: ItemKind,
    id_prefix: str,
    label: str,
) -> list[ContextItem]:
    """Load a JSONL session file into a list of ContextItems.

    This is the shared implementation used by both
    :func:`~contextweaver.adapters.mcp.load_mcp_session_jsonl` and
    :func:`~contextweaver.adapters.a2a.load_a2a_session_jsonl`.

    Each line must be a JSON object with at least:

    - ``type``: item kind string (falls back to *default_kind* if unknown)
    - ``id``: unique string identifier (auto-generated from *id_prefix* if missing)
    - ``text`` or ``content``: the textual content

    Args:
        path: Filesystem path to a JSONL file.
        default_kind: Fallback :class:`ItemKind` for unknown ``type`` values.
        id_prefix: Prefix for auto-generated IDs (e.g. ``"mcp"`` or ``"a2a"``).
        label: Human-readable protocol label for error messages
            (e.g. ``"MCP"`` or ``"A2A"``).

    Returns:
        A list of :class:`ContextItem` in file order.

    Raises:
        CatalogError: If the file cannot be read or contains invalid lines.
    """
    try:
        lines = Path(path).read_text(encoding="utf-8").strip().splitlines()
    except OSError as exc:
        raise CatalogError(f"Cannot read {label} session file: {exc}") from exc

    items: list[ContextItem] = []
    kind_map: dict[str, ItemKind] = {
        "tool_call": ItemKind.tool_call,
        "tool_result": ItemKind.tool_result,
        "user_turn": ItemKind.user_turn,
        "agent_msg": ItemKind.agent_msg,
        "doc_snippet": ItemKind.doc_snippet,
        "memory_fact": ItemKind.memory_fact,
        "plan_state": ItemKind.plan_state,
        "policy": ItemKind.policy,
    }

    for lineno, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CatalogError(f"Invalid JSON at line {lineno}: {exc}") from exc

        if not isinstance(obj, dict):
            raise CatalogError(f"Expected JSON object at line {lineno}, got {type(obj).__name__}")

        try:
            kind_str = obj.get("type", default_kind.value)
            kind = kind_map.get(kind_str, default_kind)
            text = obj.get("text") or obj.get("content", "")

            items.append(
                ContextItem(
                    id=obj.get("id", f"{id_prefix}-line-{lineno}"),
                    kind=kind,
                    text=str(text),
                    token_estimate=int(obj.get("token_estimate", 0)),
                    metadata=dict(obj.get("metadata", {})),
                    parent_id=obj.get("parent_id"),
                )
            )
        except (TypeError, ValueError, AttributeError) as exc:
            raise CatalogError(f"Invalid context item at line {lineno}: {exc}") from exc

    return items

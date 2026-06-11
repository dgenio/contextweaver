"""Exact static-schema exposure measurements for MCP gateway modes."""

from __future__ import annotations

import json
from typing import Any

from contextweaver.tokens import count
from contextweaver.types import SelectableItem

_GATEWAY_META_SCHEMAS: tuple[dict[str, Any], ...] = (
    {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "path": {"type": "string"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "additionalProperties": False,
    },
    {
        "type": "object",
        "properties": {
            "tool_id": {"type": "string"},
            "args": {"type": "object"},
        },
        "required": ["tool_id", "args"],
        "additionalProperties": False,
    },
    {
        "type": "object",
        "properties": {
            "handle": {"type": "string"},
            "selector": {"type": "object"},
        },
        "required": ["handle", "selector"],
        "additionalProperties": False,
    },
)

_PROXY_META_SCHEMAS: tuple[dict[str, Any], ...] = (
    {
        "type": "object",
        "properties": {"tool_id": {"type": "string"}},
        "required": ["tool_id"],
        "additionalProperties": False,
    },
    _GATEWAY_META_SCHEMAS[1],
)


def _schema_tokens(schema: dict[str, Any]) -> int:
    return count(json.dumps(schema, sort_keys=True, separators=(",", ":")))


def catalog_diagnostic_summary(
    items: list[SelectableItem],
    raw_defs: dict[str, dict[str, Any]],
    *,
    mode: str,
) -> dict[str, Any]:
    """Return exact catalog exposure and static schema-savings measurements."""
    namespace_counts: dict[str, int] = {}
    full_schema_tokens = 0
    for item in items:
        namespace_counts[item.namespace] = namespace_counts.get(item.namespace, 0) + 1
        full_schema_tokens += _schema_tokens(raw_defs.get(item.id, {}).get("inputSchema", {}))

    if mode == "gateway":
        exposed_count = len(_GATEWAY_META_SCHEMAS)
        exposed_schema_tokens = sum(_schema_tokens(schema) for schema in _GATEWAY_META_SCHEMAS)
    else:
        exposed_count = len(items) + len(_PROXY_META_SCHEMAS)
        exposed_schema_tokens = len(items) * _schema_tokens({"type": "object"})
        exposed_schema_tokens += sum(_schema_tokens(schema) for schema in _PROXY_META_SCHEMAS)

    return {
        "mode": mode,
        "tool_count": len(items),
        "exposed_tool_count": exposed_count,
        "namespace_counts": dict(sorted(namespace_counts.items())),
        "full_schema_tokens": full_schema_tokens,
        "exposed_schema_tokens": exposed_schema_tokens,
        "schema_tokens_avoided": max(full_schema_tokens - exposed_schema_tokens, 0),
    }

"""MCP (Model Context Protocol) adapter for contextweaver.

Converts MCP tool definitions into :class:`~contextweaver.types.SelectableItem`
objects and exposes the shared MCP-result →
:class:`~contextweaver.envelope.ResultEnvelope` transform.

Also provides :func:`load_mcp_session_jsonl` for replaying MCP sessions from
JSONL files into contextweaver :class:`~contextweaver.types.ContextItem` lists.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from contextweaver._mcp_result import mcp_result_to_envelope
from contextweaver.exceptions import CatalogError
from contextweaver.routing.tool_id import canonical_tool_id
from contextweaver.types import ContextItem, ItemKind, SelectableItem

logger = logging.getLogger("contextweaver.adapters")


def infer_namespace(tool_name: str) -> str:
    """Infer a namespace from an MCP tool name.

    Examines the tool name for common separators used by MCP servers to
    encode the server-of-origin:

    - **Dot** (``"."``): ``"github.create_issue"`` → ``"github"``
    - **Slash** (``"/"``): ``"filesystem/read"`` → ``"filesystem"``
    - **Underscore** (``"_"``): ``"slack_send_message"`` → ``"slack"``
      (only when there are 3+ segments to avoid false positives like
      ``"read_file"``)

    Falls back to ``"mcp"`` when no prefix can be detected.

    Args:
        tool_name: The raw MCP tool name string.

    Returns:
        The inferred namespace string.
    """
    if "." in tool_name:
        prefix = tool_name.split(".", 1)[0]
        if prefix:
            return prefix
    if "/" in tool_name:
        prefix = tool_name.split("/", 1)[0]
        if prefix:
            return prefix
    parts = tool_name.split("_")
    if len(parts) >= 3 and parts[0]:
        return parts[0]
    return "mcp"


def _derive_tool_id_name(upstream_name: str) -> str:
    """Derive the canonical ``tool_id`` name field from *upstream_name* (§1.4).

    The rule is keyed on the separator that :func:`infer_namespace` matched:

    - Dot or slash separator: the namespace prefix is stripped from
      ``name`` (it is unambiguously carried by the namespace field).
    - Underscore with ≥ 3 segments, or the ``mcp`` fallback: the prefix
      is **preserved** in ``name`` because underscores can appear inside
      the upstream tool name itself.

    Args:
        upstream_name: The raw upstream MCP tool name.

    Returns:
        The derived ``name`` portion of the canonical ``tool_id``.
    """
    if "." in upstream_name:
        prefix, _, rest = upstream_name.partition(".")
        if prefix and rest:
            return rest
    if "/" in upstream_name:
        prefix, _, rest = upstream_name.partition("/")
        if prefix and rest:
            return rest
    return upstream_name


def mcp_tool_to_selectable(tool_def: dict[str, Any]) -> SelectableItem:
    """Convert an MCP tool definition dict to a :class:`SelectableItem`.

    Expected keys in *tool_def*:

    - ``name`` (required)
    - ``description`` (required)
    - ``inputSchema`` (optional JSON Schema dict)
    - ``outputSchema`` (optional JSON Schema dict for structured output)
    - ``annotations`` (optional dict with ``title``, ``readOnlyHint``,
      ``destructiveHint``, ``costHint``, etc.)

    Warning:
        MCP annotations (``readOnlyHint``, ``destructiveHint``, ``costHint``)
        are **server-declared hints**, not verified security properties. Do not
        make access-control or safety-critical decisions based solely on these
        values. Use a separate capability- or policy-based authorization
        mechanism rather than relying on these hints.

    Args:
        tool_def: Raw MCP tool definition as returned by ``tools/list``.

    Returns:
        A :class:`SelectableItem` with ``kind="tool"`` and an inferred
        namespace.

    Raises:
        CatalogError: If required fields are missing.
    """
    name = tool_def.get("name")
    description = tool_def.get("description")
    if not name or not description:
        missing: list[str] = []
        if not name:
            missing.append("name")
        if not description:
            missing.append("description")
        raise CatalogError(f"MCP tool definition missing required fields: {missing}")

    annotations: dict[str, Any] = tool_def.get("annotations") or {}
    input_schema: dict[str, Any] = tool_def.get("inputSchema") or {}
    output_schema_raw: dict[str, Any] | None = tool_def.get("outputSchema")
    output_schema: dict[str, Any] | None = (
        dict(output_schema_raw) if output_schema_raw is not None else None
    )

    # MCP annotations are server-declared hints only. They are useful routing
    # metadata but are not authorization evidence.
    tags: list[str] = ["mcp"]
    if annotations.get("readOnlyHint", False):
        tags.append("read-only")
    if annotations.get("destructiveHint", False):
        tags.append("destructive")

    side_effects = not annotations.get("readOnlyHint", False)
    cost_hint = float(annotations.get("costHint", 0.0))

    upstream_name = str(name)
    namespace = infer_namespace(upstream_name)
    derived_name = _derive_tool_id_name(upstream_name)
    meta_block = tool_def.get("_meta") or {}
    version_raw = meta_block.get("version") if isinstance(meta_block, dict) else None
    version = str(version_raw) if version_raw else None
    tool_id = canonical_tool_id(
        namespace=namespace,
        name=derived_name,
        upstream_name=upstream_name,
        input_schema=input_schema,
        version=version,
    )

    logger.debug(
        "mcp_tool_to_selectable: name=%s, tool_id=%s, tags=%s", name, tool_id, sorted(tags)
    )
    return SelectableItem(
        id=tool_id,
        kind="tool",
        name=upstream_name,
        description=str(description),
        tags=sorted(tags),
        namespace=namespace,
        args_schema=dict(input_schema),
        output_schema=output_schema,
        side_effects=side_effects,
        cost_hint=cost_hint,
        metadata={k: v for k, v in annotations.items() if k != "costHint"},
    )


def load_mcp_session_jsonl(path: str | Path) -> list[ContextItem]:
    """Load an MCP session from a JSONL file into a list of ContextItems.

    Each line must be a JSON object with at least:

    - ``type``: one of ``"tool_call"``, ``"tool_result"``, ``"user_turn"``,
      ``"agent_msg"``
    - ``id``: unique string identifier
    - ``text`` or ``content``: the textual content

    Tool results are linked to their tool calls via ``parent_id``.

    Args:
        path: Filesystem path to a JSONL file.

    Returns:
        A list of :class:`ContextItem` in file order.

    Raises:
        CatalogError: If the file cannot be read or contains invalid lines.
    """
    from contextweaver.adapters._common import _load_session_jsonl

    return _load_session_jsonl(
        path,
        default_kind=ItemKind.user_turn,
        id_prefix="mcp",
        label="MCP",
    )


__all__ = [
    "infer_namespace",
    "load_mcp_session_jsonl",
    "mcp_result_to_envelope",
    "mcp_tool_to_selectable",
]

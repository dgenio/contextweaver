"""MCP (Model Context Protocol) adapter for contextweaver.

Converts MCP tool definitions into :class:`~contextweaver.types.SelectableItem`
objects and wraps MCP tool call results as
:class:`~contextweaver.envelope.ResultEnvelope` instances.

Also provides :func:`load_mcp_session_jsonl` for replaying MCP sessions from
JSONL files into contextweaver :class:`~contextweaver.types.ContextItem` lists.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from contextweaver.envelope import ResultEnvelope
from contextweaver.exceptions import CatalogError
from contextweaver.types import ArtifactRef, ContextItem, ItemKind, SelectableItem


def mcp_tool_to_selectable(tool_def: dict[str, Any]) -> SelectableItem:
    """Convert an MCP tool definition dict to a :class:`SelectableItem`.

    Expected keys in *tool_def*:

    - ``name`` (required)
    - ``description`` (required)
    - ``inputSchema`` (optional JSON Schema dict)
    - ``annotations`` (optional dict with ``title``, ``readOnlyHint``,
      ``destructiveHint``, ``costHint``, etc.)

    Args:
        tool_def: Raw MCP tool definition as returned by ``tools/list``.

    Returns:
        A :class:`SelectableItem` with ``kind="tool"`` and
        ``namespace="mcp"``.

    Raises:
        CatalogError: If required fields (``name``, ``description``) are
            missing from the definition.
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

    # Derive tags from annotation hints
    tags: list[str] = ["mcp"]
    if annotations.get("readOnlyHint", False):
        tags.append("read-only")
    if annotations.get("destructiveHint", False):
        tags.append("destructive")

    side_effects = not annotations.get("readOnlyHint", False)
    cost_hint = float(annotations.get("costHint", 0.0))

    return SelectableItem(
        id=f"mcp:{name}",
        kind="tool",
        name=str(name),
        description=str(description),
        tags=sorted(tags),
        namespace="mcp",
        args_schema=dict(input_schema),
        side_effects=side_effects,
        cost_hint=cost_hint,
        metadata={k: v for k, v in annotations.items() if k != "costHint"},
    )


def mcp_result_to_envelope(
    result: dict[str, Any],
    tool_name: str,
) -> ResultEnvelope:
    """Convert an MCP tool call result to a :class:`ResultEnvelope`.

    The MCP result dict is expected to have:

    - ``content`` — a list of content parts, each with ``type`` and
      ``text`` (or ``data`` / ``resource``).
    - ``isError`` (optional bool) — if ``True``, status becomes ``"error"``.

    .. note::

        Returned :class:`~contextweaver.types.ArtifactRef` entries are
        **metadata-only** — the underlying data is not persisted to an
        :class:`~contextweaver.protocols.ArtifactStore`.  Callers that
        need resolvable handles should store the raw data separately
        (e.g. via :meth:`ContextManager.ingest_tool_result`).

    Args:
        result: Raw MCP tool result dict.
        tool_name: The name of the tool that produced the result.

    Returns:
        A :class:`ResultEnvelope`.
    """
    is_error = bool(result.get("isError", False))
    content_parts: list[dict[str, Any]] = result.get("content") or []

    text_parts: list[str] = []
    artifacts: list[ArtifactRef] = []

    for i, part in enumerate(content_parts):
        part_type = part.get("type", "text")
        if part_type == "text":
            text_parts.append(part.get("text", ""))
        elif part_type == "image":
            mime = part.get("mimeType", "image/png")
            data_str = part.get("data", "")
            artifacts.append(
                ArtifactRef(
                    handle=f"mcp:{tool_name}:image:{i}",
                    media_type=mime,
                    size_bytes=len(data_str),
                    label=f"image from {tool_name}",
                )
            )
        elif part_type == "resource":
            resource: dict[str, Any] = part.get("resource", {})
            mime = resource.get("mimeType", "application/octet-stream")
            uri = resource.get("uri", "")
            text_content = resource.get("text", "")
            if text_content:
                text_parts.append(str(text_content))
            artifacts.append(
                ArtifactRef(
                    handle=f"mcp:{tool_name}:resource:{i}",
                    media_type=mime,
                    size_bytes=len(str(text_content)),
                    label=uri or f"resource from {tool_name}",
                )
            )

    summary = "\n".join(text_parts) if text_parts else "(no content)"

    status: Literal["ok", "partial", "error"] = "error" if is_error else "ok"

    # Simple fact extraction from key-value lines
    facts: list[str] = []
    for part_text in text_parts:
        for line in part_text.splitlines():
            stripped = line.strip()
            if ":" in stripped and len(stripped) < 200:
                facts.append(stripped)

    return ResultEnvelope(
        status=status,
        summary=summary[:500] if len(summary) > 500 else summary,
        facts=facts[:20],
        artifacts=artifacts,
        provenance={"tool": tool_name, "protocol": "mcp"},
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
    try:
        lines = Path(path).read_text(encoding="utf-8").strip().splitlines()
    except OSError as exc:
        raise CatalogError(f"Cannot read MCP session file: {exc}") from exc

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
            kind_str = obj.get("type", "user_turn")
            kind = kind_map.get(kind_str, ItemKind.user_turn)
            text = obj.get("text") or obj.get("content", "")

            items.append(
                ContextItem(
                    id=obj.get("id", f"mcp-line-{lineno}"),
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

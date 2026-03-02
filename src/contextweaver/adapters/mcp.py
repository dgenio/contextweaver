"""MCP (Model Context Protocol) adapter for contextweaver.

Converts MCP tool definitions into :class:`~contextweaver.types.SelectableItem`
objects and wraps MCP tool call results as
:class:`~contextweaver.envelope.ResultEnvelope` instances.

Also provides :func:`load_mcp_session_jsonl` for replaying MCP sessions from
JSONL files into contextweaver :class:`~contextweaver.types.ContextItem` lists.
"""

from __future__ import annotations

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
) -> tuple[ResultEnvelope, dict[str, tuple[bytes, str, str]]]:
    """Convert an MCP tool call result to a :class:`ResultEnvelope`.

    The MCP result dict is expected to have:

    - ``content`` — a list of content parts, each with ``type`` and
      ``text`` (or ``data`` / ``resource``).
    - ``isError`` (optional bool) — if ``True``, status becomes ``"error"``.

    Returns both the envelope and a dict of binary data extracted from
    image and resource content parts.  Use
    :meth:`ContextManager.ingest_mcp_result` for the full happy path
    that persists artifacts automatically.

    Args:
        result: Raw MCP tool result dict.
        tool_name: The name of the tool that produced the result.

    Returns:
        A ``(ResultEnvelope, binaries)`` tuple where *binaries* maps
        ``handle -> (raw_bytes, media_type, label)``.
    """
    import base64 as _b64

    is_error = bool(result.get("isError", False))
    content_parts: list[dict[str, Any]] = result.get("content") or []

    text_parts: list[str] = []
    artifacts: list[ArtifactRef] = []
    binaries: dict[str, tuple[bytes, str, str]] = {}

    for i, part in enumerate(content_parts):
        part_type = part.get("type", "text")
        if part_type == "text":
            text_parts.append(part.get("text", ""))
        elif part_type == "image":
            mime = part.get("mimeType", "image/png")
            data_str = part.get("data", "")
            handle = f"mcp:{tool_name}:image:{i}"
            # Decode base64 image data; fall back to raw bytes on error.
            try:
                raw = _b64.b64decode(data_str)
            except Exception:  # noqa: BLE001
                raw = data_str.encode("utf-8")
            artifacts.append(
                ArtifactRef(
                    handle=handle,
                    media_type=mime,
                    size_bytes=len(raw),
                    label=f"image from {tool_name}",
                )
            )
            binaries[handle] = (raw, mime, f"image from {tool_name}")
        elif part_type == "resource":
            resource: dict[str, Any] = part.get("resource", {})
            mime = resource.get("mimeType", "application/octet-stream")
            uri = resource.get("uri", "")
            text_content = resource.get("text", "")
            if text_content:
                text_parts.append(str(text_content))
            handle = f"mcp:{tool_name}:resource:{i}"
            raw = str(text_content).encode("utf-8")
            label = uri or f"resource from {tool_name}"
            artifacts.append(
                ArtifactRef(
                    handle=handle,
                    media_type=mime,
                    size_bytes=len(raw),
                    label=label,
                )
            )
            binaries[handle] = (raw, mime, label)

    summary = "\n".join(text_parts) if text_parts else "(no content)"

    status: Literal["ok", "partial", "error"] = "error" if is_error else "ok"

    # Simple fact extraction from key-value lines
    facts: list[str] = []
    for part_text in text_parts:
        for line in part_text.splitlines():
            stripped = line.strip()
            if ":" in stripped and len(stripped) < 200:
                facts.append(stripped)

    envelope = ResultEnvelope(
        status=status,
        summary=summary[:500] if len(summary) > 500 else summary,
        facts=facts[:20],
        artifacts=artifacts,
        provenance={"tool": tool_name, "protocol": "mcp"},
    )
    return envelope, binaries


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

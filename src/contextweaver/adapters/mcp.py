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


def mcp_tool_to_selectable(tool_def: dict[str, Any]) -> SelectableItem:
    """Convert an MCP tool definition dict to a :class:`SelectableItem`.

    Expected keys in *tool_def*:

    - ``name`` (required)
    - ``description`` (required)
    - ``inputSchema`` (optional JSON Schema dict)
    - ``outputSchema`` (optional JSON Schema dict for structured output)
    - ``annotations`` (optional dict with ``title``, ``readOnlyHint``,
      ``destructiveHint``, ``costHint``, etc.)

    Args:
        tool_def: Raw MCP tool definition as returned by ``tools/list``.

    Returns:
        A :class:`SelectableItem` with ``kind="tool"`` and a namespace
        inferred from the tool name (see :func:`infer_namespace`).

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
    output_schema_raw: dict[str, Any] | None = tool_def.get("outputSchema")
    output_schema: dict[str, Any] | None = (
        dict(output_schema_raw) if output_schema_raw is not None else None
    )

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
        namespace=infer_namespace(str(name)),
        args_schema=dict(input_schema),
        output_schema=output_schema,
        side_effects=side_effects,
        cost_hint=cost_hint,
        metadata={k: v for k, v in annotations.items() if k != "costHint"},
    )


def _decode_binary_part(
    part: dict[str, Any],
    tool_name: str,
    index: int,
    default_mime: str,
    kind: str,
) -> tuple[ArtifactRef, tuple[bytes, str, str]]:
    """Decode a base64-encoded content part (image, audio, etc.)."""
    import base64 as _b64

    mime = part.get("mimeType", default_mime)
    data_str = part.get("data") or ""
    handle = f"mcp:{tool_name}:{kind}:{index}"
    label = f"{kind} from {tool_name}"
    try:
        raw = _b64.b64decode(data_str, validate=True)
    except Exception:  # noqa: BLE001
        raw = data_str if isinstance(data_str, bytes) else str(data_str).encode("utf-8")
    ref = ArtifactRef(
        handle=handle,
        media_type=mime,
        size_bytes=len(raw),
        label=label,
    )
    return ref, (raw, mime, label)


def mcp_result_to_envelope(
    result: dict[str, Any],
    tool_name: str,
) -> tuple[ResultEnvelope, dict[str, tuple[bytes, str, str]], str]:
    """Convert an MCP tool call result to a :class:`ResultEnvelope`.

    The MCP result dict is expected to have:

    - ``content`` — a list of content parts, each with ``type`` and
      ``text`` (or ``data`` / ``resource``).  Supported content types:
      ``text``, ``image``, ``resource``, ``resource_link``, ``audio``.
    - ``structuredContent`` (optional) — a JSON value with typed tool
      output; stored as a structured artifact.  Only dicts get fact extraction.
    - ``isError`` (optional bool) — if ``True``, status becomes ``"error"``.

    Each content part may carry per-part ``annotations`` with ``audience``
    and ``priority`` fields.  These are collected into the envelope's
    ``provenance["content_annotations"]`` list.

    Returns the envelope, a dict of binary data extracted from image,
    audio, and resource content parts, and the full (untruncated) text.
    Use :meth:`ContextManager.ingest_mcp_result` for the full happy path
    that persists artifacts automatically.

    Args:
        result: Raw MCP tool result dict.
        tool_name: The name of the tool that produced the result.

    Returns:
        A ``(ResultEnvelope, binaries, full_text)`` tuple where *binaries*
        maps ``handle -> (raw_bytes, media_type, label)`` and *full_text*
        is the complete untruncated text content.
    """
    import json as _json

    is_error = bool(result.get("isError", False))
    content_parts: list[dict[str, Any]] = result.get("content") or []
    # MCP spec allows any JSON value; only dicts get fact extraction.
    structured_content: Any = result.get("structuredContent")

    text_parts: list[str] = []
    artifacts: list[ArtifactRef] = []
    binaries: dict[str, tuple[bytes, str, str]] = {}
    content_annotations: list[dict[str, Any]] = []

    for i, part in enumerate(content_parts):
        part_type = part.get("type", "text")

        # Collect per-part annotations (audience / priority)
        part_annotations = part.get("annotations")
        if isinstance(part_annotations, dict) and part_annotations:
            content_annotations.append({"part_index": i, **part_annotations})

        if part_type == "text":
            text_parts.append(part.get("text", ""))
        elif part_type == "image":
            ref, blob = _decode_binary_part(part, tool_name, i, "image/png", "image")
            artifacts.append(ref)
            binaries[ref.handle] = blob
        elif part_type == "audio":
            ref, blob = _decode_binary_part(part, tool_name, i, "audio/wav", "audio")
            artifacts.append(ref)
            binaries[ref.handle] = blob
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
        elif part_type == "resource_link":
            uri = part.get("uri", "")
            mime = part.get("mimeType", "application/octet-stream")
            name = part.get("name", "")
            handle = f"mcp:{tool_name}:resource_link:{i}"
            label = name or uri or f"resource link from {tool_name}"
            uri_bytes = uri.encode("utf-8")
            artifacts.append(
                ArtifactRef(
                    handle=handle,
                    media_type=mime,
                    size_bytes=len(uri_bytes),
                    label=label,
                )
            )
            # No dereferenced payload — resource_link is a URI reference only.
            # Store the URI bytes so callers can resolve the URI themselves.
            # Use text/uri-list (RFC 2483) since the payload is a URI, not the
            # linked resource; the resource's declared MIME stays in ArtifactRef.
            binaries[handle] = (uri_bytes, "text/uri-list", label)

    # Handle structuredContent (top-level JSON output per MCP spec)
    if structured_content is not None:
        sc_handle = f"mcp:{tool_name}:structured_content"
        sc_bytes = _json.dumps(structured_content, sort_keys=True).encode("utf-8")
        artifacts.append(
            ArtifactRef(
                handle=sc_handle,
                media_type="application/json",
                size_bytes=len(sc_bytes),
                label=f"structured content from {tool_name}",
            )
        )
        binaries[sc_handle] = (sc_bytes, "application/json", f"structured content from {tool_name}")
        # Extract facts from top-level keys when structured_content is a mapping.
        if isinstance(structured_content, dict):
            for key, value in structured_content.items():
                rendered = str(value)
                if len(rendered) < 200:
                    facts_line = f"{key}: {rendered}"
                    text_parts.append(facts_line)

    full_text = "\n".join(text_parts) if text_parts else "(no content)"

    status: Literal["ok", "partial", "error"] = "error" if is_error else "ok"

    # Simple fact extraction from key-value lines
    facts: list[str] = []
    for part_text in text_parts:
        for line in part_text.splitlines():
            stripped = line.strip()
            if ":" in stripped and len(stripped) < 200:
                facts.append(stripped)

    provenance: dict[str, Any] = {"tool": tool_name, "protocol": "mcp"}
    if content_annotations:
        provenance["content_annotations"] = content_annotations

    envelope = ResultEnvelope(
        status=status,
        summary=full_text[:500] if len(full_text) > 500 else full_text,
        facts=facts[:20],
        artifacts=artifacts,
        provenance=provenance,
    )
    return envelope, binaries, full_text


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

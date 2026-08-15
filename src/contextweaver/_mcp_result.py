"""Provider-neutral storage shape for MCP result ingestion.

This module owns the pure ``MCP result dict -> ResultEnvelope`` transform used
by both the MCP adapter surface and the core context-ingestion path. Keeping the
transform below ``adapters/`` prevents core ``context/`` code from importing an
adapter merely to reuse pure result shaping (issue #752).

The existing ``contextweaver.adapters.mcp.mcp_result_to_envelope`` import path
remains available as a compatibility re-export.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any, Literal

from contextweaver.envelope import ResultEnvelope
from contextweaver.types import ArtifactRef

logger = logging.getLogger("contextweaver.adapters")


def _decode_binary_part(
    part: dict[str, Any],
    tool_name: str,
    index: int,
    default_mime: str,
    kind: str,
) -> tuple[ArtifactRef, tuple[bytes, str, str]]:
    """Decode a base64-encoded MCP content part."""
    mime = part.get("mimeType", default_mime)
    data_str = part.get("data") or ""
    handle = f"mcp:{tool_name}:{kind}:{index}"
    label = f"{kind} from {tool_name}"
    try:
        raw = base64.b64decode(data_str, validate=True)
    except Exception:  # noqa: BLE001 -- malformed payload is retained as bytes
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

    The transform is intentionally pure: it does not touch stores or runtime
    adapters. Binary payloads are returned separately for the caller to persist.
    """
    is_error = bool(result.get("isError", False))
    content_parts: list[dict[str, Any]] = result.get("content") or []
    structured_content: Any = result.get("structuredContent")

    text_parts: list[str] = []
    artifacts: list[ArtifactRef] = []
    binaries: dict[str, tuple[bytes, str, str]] = {}
    content_annotations: list[dict[str, Any]] = []

    for i, part in enumerate(content_parts):
        part_type = part.get("type", "text")

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
            binaries[handle] = (uri_bytes, "text/uri-list", label)

    if structured_content is not None:
        sc_handle = f"mcp:{tool_name}:structured_content"
        sc_bytes = json.dumps(structured_content, sort_keys=True).encode("utf-8")
        sc_label = f"structured content from {tool_name}"
        artifacts.append(
            ArtifactRef(
                handle=sc_handle,
                media_type="application/json",
                size_bytes=len(sc_bytes),
                label=sc_label,
            )
        )
        binaries[sc_handle] = (sc_bytes, "application/json", sc_label)
        if isinstance(structured_content, dict):
            for key, value in structured_content.items():
                rendered = str(value)
                if len(rendered) < 200:
                    text_parts.append(f"{key}: {rendered}")

    full_text = "\n".join(text_parts) if text_parts else "(no content)"
    status: Literal["ok", "partial", "error"] = "error" if is_error else "ok"

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
    logger.debug(
        "mcp_result_to_envelope: tool=%s, status=%s, artifacts=%d, facts=%d",
        tool_name,
        status,
        len(artifacts),
        len(envelope.facts),
    )
    return envelope, binaries, full_text


__all__ = ["mcp_result_to_envelope"]

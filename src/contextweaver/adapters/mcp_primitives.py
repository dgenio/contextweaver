"""MCP resource/prompt → :class:`SelectableItem` converters (#669 / #670).

Extends the tool-only :mod:`contextweaver.adapters.mcp` adapter to the other
two MCP primitives so the gateway can shape resources and prompts with the same
bounded-choice routing and context-firewall treatment it applies to tools
(#555).  Each converter is a pure, stateless function (the ``adapters/`` rule)
and emits ids through the shared cross-primitive identity policy in
:mod:`contextweaver.routing.primitive_id` (#671).

- :func:`mcp_resource_to_selectable` — a ``resources/list`` entry
  (``uri`` / ``name`` / ``mimeType`` / ``description``) →
  ``SelectableItem(kind="resource")``.  The URI is preserved in
  ``metadata["uri"]`` because reads address the resource by URI, not by name.
- :func:`mcp_prompt_to_selectable` — a ``prompts/list`` entry
  (``name`` / ``description`` / ``arguments``) →
  ``SelectableItem(kind="prompt")``.  Declared arguments become an
  ``args_schema`` so ``prompt_get`` can validate inputs exactly like
  ``tool_execute``.
- :func:`mcp_resource_read_to_envelope` / :func:`mcp_prompt_get_to_envelope` —
  wrap a ``resources/read`` / ``prompts/get`` result as a
  :class:`~contextweaver.envelope.ResultEnvelope` (+ extracted binaries + full
  text) so the firewall can compact large reads.
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
from typing import Any

from contextweaver.adapters.mcp import infer_namespace
from contextweaver.envelope import ResultEnvelope
from contextweaver.exceptions import CatalogError
from contextweaver.routing.primitive_id import canonical_prompt_id, canonical_resource_id
from contextweaver.types import ArtifactRef

logger = logging.getLogger("contextweaver.adapters.mcp_primitives")

_NAMESPACE_CLEAN_RE = re.compile(r"[^a-z0-9_-]")
_NAME_CLEAN_RE = re.compile(r"[^A-Za-z0-9_.\-]")


def _sanitize_namespace(raw: str) -> str:
    """Coerce *raw* into a §1.1-valid namespace (``[a-z][a-z0-9_-]{0,63}``)."""
    cleaned = _NAMESPACE_CLEAN_RE.sub("-", (raw or "").lower())
    cleaned = cleaned.strip("-_")
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"mcp{('-' + cleaned) if cleaned else ''}"
    return cleaned[:64]


def _sanitize_name(raw: str) -> str:
    """Coerce *raw* into a §1.1-valid name (``[A-Za-z_][A-Za-z0-9_.-]{0,127}``)."""
    cleaned = _NAME_CLEAN_RE.sub("_", raw or "")
    cleaned = cleaned.strip(".-") or "item"
    if not (cleaned[0].isalpha() or cleaned[0] == "_"):
        cleaned = f"r_{cleaned}"
    return cleaned[:128]


def infer_resource_namespace(uri: str) -> str:
    """Infer a namespace from a resource URI's scheme (e.g. ``file``, ``postgres``).

    Falls back to ``"mcp"`` when no scheme is present.

    Args:
        uri: The resource ``uri`` (e.g. ``file:///docs/readme.md``).

    Returns:
        A §1.1-valid namespace string.
    """
    scheme, sep, _ = (uri or "").partition("://")
    if not sep:
        scheme, sep, _ = (uri or "").partition(":")
    return _sanitize_namespace(scheme if sep else "mcp")


def _resource_name(uri: str, declared: str) -> str:
    """Derive a resource name from its declared name or URI path tail."""
    if declared:
        return _sanitize_name(declared)
    tail = (uri or "").rstrip("/").rsplit("/", 1)[-1]
    return _sanitize_name(tail or uri or "resource")


def mcp_resource_to_selectable(resource_def: dict[str, Any]) -> Any:  # noqa: ANN401
    """Convert an MCP ``resources/list`` entry to a ``resource`` SelectableItem.

    Args:
        resource_def: Raw MCP resource definition.  Requires a non-empty
            string ``uri``; ``name`` / ``description`` / ``mimeType`` are
            optional.

    Returns:
        A :class:`~contextweaver.types.SelectableItem` with ``kind="resource"``,
        the URI preserved in ``metadata["uri"]``, and ``side_effects=False``
        (reads never mutate upstream state).

    Raises:
        CatalogError: If ``uri`` is missing or not a non-empty string.
    """
    from contextweaver.types import SelectableItem

    uri = resource_def.get("uri")
    if not isinstance(uri, str) or not uri.strip():
        raise CatalogError("MCP resource definition missing required non-empty string 'uri'")
    declared_name = resource_def.get("name")
    description = resource_def.get("description") or declared_name or uri
    mime = resource_def.get("mimeType", "")
    namespace = infer_resource_namespace(uri)
    name = _resource_name(uri, declared_name if isinstance(declared_name, str) else "")
    resource_id = canonical_resource_id(namespace=namespace, name=name, uri=uri)
    tags = ["mcp", "resource", "read-only"]
    if mime:
        tags.append(_sanitize_name(mime).lower()[:24])
    return SelectableItem(
        id=resource_id,
        kind="resource",
        name=str(declared_name or uri),
        description=str(description),
        tags=sorted(set(tags)),
        namespace=namespace,
        side_effects=False,
        metadata={"uri": uri, "mime_type": mime, "primitive": "resource"},
    )


def _prompt_args_schema(arguments: list[dict[str, Any]] | None) -> tuple[dict[str, Any], list[str]]:
    """Build a JSON-Schema object + sorted arg-name list from prompt arguments."""
    props: dict[str, Any] = {}
    required: list[str] = []
    names: list[str] = []
    for arg in arguments or []:
        if not isinstance(arg, dict):
            continue
        arg_name = arg.get("name")
        if not isinstance(arg_name, str) or not arg_name:
            continue
        names.append(arg_name)
        prop: dict[str, Any] = {"type": "string"}
        if arg.get("description"):
            prop["description"] = str(arg["description"])
        props[arg_name] = prop
        if arg.get("required"):
            required.append(arg_name)
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = sorted(required)
    return schema, names


def mcp_prompt_to_selectable(prompt_def: dict[str, Any]) -> Any:  # noqa: ANN401
    """Convert an MCP ``prompts/list`` entry to a ``prompt`` SelectableItem.

    Args:
        prompt_def: Raw MCP prompt definition.  Requires a non-empty string
            ``name``; ``description`` / ``arguments`` are optional.

    Returns:
        A :class:`~contextweaver.types.SelectableItem` with ``kind="prompt"``;
        declared arguments are mapped to ``args_schema`` so ``prompt_get`` can
        validate them.  ``side_effects=False`` — fetching a prompt is read-only.

    Raises:
        CatalogError: If ``name`` is missing or not a non-empty string.
    """
    from contextweaver.types import SelectableItem

    name = prompt_def.get("name")
    if not isinstance(name, str) or not name.strip():
        raise CatalogError("MCP prompt definition missing required non-empty string 'name'")
    arguments = prompt_def.get("arguments")
    args_schema, arg_names = _prompt_args_schema(arguments if isinstance(arguments, list) else None)
    namespace = infer_namespace(name)
    derived = _sanitize_name(name)
    prompt_id = canonical_prompt_id(namespace=namespace, name=derived, argument_names=arg_names)
    description = prompt_def.get("description") or name
    return SelectableItem(
        id=prompt_id,
        kind="prompt",
        name=str(name),
        description=str(description),
        tags=sorted({"mcp", "prompt"}),
        namespace=namespace,
        args_schema=args_schema,
        side_effects=False,
        metadata={"prompt_name": name, "primitive": "prompt", "arguments": arg_names},
    )


def mcp_resource_read_to_envelope(
    result: dict[str, Any], resource_id: str
) -> tuple[ResultEnvelope, dict[str, tuple[bytes, str, str]], str]:
    """Wrap an MCP ``resources/read`` result as a :class:`ResultEnvelope`.

    The result's ``contents`` list carries one or more ``{uri, mimeType, text |
    blob}`` parts.  Text parts are concatenated into the envelope summary/full
    text; every part is also persisted as a binary so ``tool_view`` can drill
    into large reads.

    Args:
        result: Raw MCP ``resources/read`` result dict.
        resource_id: Canonical resource id (for provenance + artifact handles).

    Returns:
        A ``(ResultEnvelope, binaries, full_text)`` tuple.
    """
    contents: list[dict[str, Any]] = result.get("contents") or []
    text_parts: list[str] = []
    artifacts: list[ArtifactRef] = []
    binaries: dict[str, tuple[bytes, str, str]] = {}
    for i, part in enumerate(contents):
        if not isinstance(part, dict):
            continue
        mime = part.get("mimeType", "text/plain")
        uri = part.get("uri", "")
        text = part.get("text")
        if isinstance(text, str):
            text_parts.append(text)
            raw = text.encode("utf-8")
        else:
            # MCP `blob` payloads are base64-encoded binary; decode them back to
            # the original bytes so persisted artifacts and `tool_view` drilldown
            # stay byte-accurate (storing the base64 text bytes corrupts real
            # binary resources). Malformed (non-base64) blobs fall back to raw bytes.
            blob = part.get("blob")
            mime = part.get("mimeType", "application/octet-stream")
            if isinstance(blob, str):
                try:
                    raw = base64.b64decode(blob, validate=True)
                except (binascii.Error, ValueError):
                    raw = blob.encode("utf-8")
            else:
                raw = b""
        handle = f"resource:{resource_id}:{i}"
        label = uri or f"content from {resource_id}"
        artifacts.append(
            ArtifactRef(handle=handle, media_type=mime, size_bytes=len(raw), label=label)
        )
        binaries[handle] = (raw, mime, label)
    full_text = "\n".join(text_parts) if text_parts else "(no content)"
    envelope = ResultEnvelope(
        status="ok",
        summary=full_text[:500],
        facts=[],
        artifacts=artifacts,
        provenance={"resource_id": resource_id, "protocol": "mcp", "primitive": "resource"},
    )
    return envelope, binaries, full_text


def mcp_prompt_get_to_envelope(
    result: dict[str, Any], prompt_id: str
) -> tuple[ResultEnvelope, dict[str, tuple[bytes, str, str]], str]:
    """Wrap an MCP ``prompts/get`` result as a :class:`ResultEnvelope`.

    The result carries an optional ``description`` and a ``messages`` list of
    ``{role, content}`` entries; message text is rendered into the envelope so
    the firewall can compact long prompt templates.

    Args:
        result: Raw MCP ``prompts/get`` result dict.
        prompt_id: Canonical prompt id (for provenance + artifact handle).

    Returns:
        A ``(ResultEnvelope, binaries, full_text)`` tuple.
    """
    messages: list[dict[str, Any]] = result.get("messages") or []
    rendered: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", "user"))
        content = message.get("content")
        if isinstance(content, dict):
            text = str(content.get("text", ""))
        elif isinstance(content, str):
            text = content
        else:
            text = ""
        rendered.append(f"[{role}] {text}".rstrip())
    full_text = "\n".join(rendered) if rendered else "(no messages)"
    raw = full_text.encode("utf-8")
    handle = f"prompt:{prompt_id}:messages"
    label = f"rendered prompt {prompt_id}"
    binaries = {handle: (raw, "text/plain", label)}
    artifacts = [
        ArtifactRef(handle=handle, media_type="text/plain", size_bytes=len(raw), label=label)
    ]
    description = result.get("description")
    envelope = ResultEnvelope(
        status="ok",
        summary=(str(description) if description else full_text[:500]),
        facts=[],
        artifacts=artifacts,
        provenance={"prompt_id": prompt_id, "protocol": "mcp", "primitive": "prompt"},
    )
    return envelope, binaries, full_text

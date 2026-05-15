"""Canonical ``tool_id`` parsing and formatting.

Implements the grammar and hash function from
:doc:`docs/gateway_spec.md` §1.  A canonical ``tool_id`` is::

    tool_id   = namespace ":" name [ "@" version ] [ "#" hash8 ]
    namespace = [a-z] [a-z0-9_-]{0,63}
    name      = [A-Za-z_] [A-Za-z0-9_.-]{0,127}
    version   = [A-Za-z0-9._-]{1,32}
    hash8     = [0-9a-f]{8}

Total length is bounded at 240 characters.  ``hash8`` is **required** when
``version`` is absent.

Public API:
    - :class:`ToolIdParts` — destructured form of a canonical id.
    - :func:`parse_tool_id` — string → :class:`ToolIdParts`.
    - :func:`format_tool_id` — :class:`ToolIdParts` → string.
    - :func:`compute_hash8` — sha256-based 8-char shape hash (§1.3).
    - :func:`canonical_tool_id` — assemble a canonical id from upstream
      metadata in one call.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from contextweaver.exceptions import CatalogError

_NAMESPACE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]{0,127}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9._\-]{1,32}$")
_HASH8_RE = re.compile(r"^[0-9a-f]{8}$")

_TOOL_ID_MAX_LEN = 240


@dataclass(frozen=True)
class ToolIdParts:
    """Destructured form of a canonical ``tool_id``.

    Attributes:
        namespace: Lowercase prefix identifying the upstream server.
        name: Tool name derived from the upstream tool's name (see §1.2/§1.4).
        version: Optional upstream-declared version string.
        hash8: Optional 8-char lowercase hex digest of the input-schema shape.
            Required when *version* is ``None`` so the id is stable across
            description-only updates.
    """

    namespace: str
    name: str
    version: str | None = None
    hash8: str | None = None


def _canonical_shape(input_schema: dict[str, Any] | None) -> str:
    """Return the canonical JSON form of an input-schema *shape* (§1.3).

    Only top-level ``properties`` keys and the ``required`` array are
    considered — types and descriptions are deliberately excluded so that
    prose-only edits do not churn the id.
    """
    schema = input_schema or {}
    props = sorted((schema.get("properties") or {}).keys())
    required = sorted(schema.get("required") or [])
    return json.dumps(
        {"properties": props, "required": required},
        sort_keys=True,
        separators=(",", ":"),
    )


def compute_hash8(upstream_name: str, input_schema: dict[str, Any] | None) -> str:
    """Compute the 8-char shape hash of an MCP tool (§1.3).

    The hash is taken over ``upstream_name + "\\n" + canonical_shape``.
    Including the upstream name disambiguates tools that share an
    input-schema shape but originate in different namespaces (e.g.
    ``github.create_issue`` vs ``gitlab.create_issue``).

    Args:
        upstream_name: The original tool name as reported by the upstream
            server, *before* any namespace stripping.
        input_schema: The tool's MCP ``inputSchema`` dict.  ``None`` and
            ``{}`` are treated as the empty schema.

    Returns:
        An 8-character lowercase hex string.
    """
    canonical = upstream_name + "\n" + _canonical_shape(input_schema)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]


def format_tool_id(parts: ToolIdParts) -> str:
    """Assemble a canonical ``tool_id`` from its parts (§1.6).

    Args:
        parts: A :class:`ToolIdParts` instance.

    Returns:
        The canonical string form.

    Raises:
        CatalogError: If any field violates the §1.1 grammar, the total
            length exceeds 240 characters, or both ``version`` and ``hash8``
            are absent.
    """
    if not _NAMESPACE_RE.match(parts.namespace):
        raise CatalogError(f"tool_id namespace not matching grammar: {parts.namespace!r}")
    if not _NAME_RE.match(parts.name):
        raise CatalogError(f"tool_id name not matching grammar: {parts.name!r}")
    if parts.version is not None and not _VERSION_RE.match(parts.version):
        raise CatalogError(f"tool_id version not matching grammar: {parts.version!r}")
    if parts.hash8 is not None and not _HASH8_RE.match(parts.hash8):
        raise CatalogError(f"tool_id hash8 not matching grammar: {parts.hash8!r}")
    if parts.version is None and parts.hash8 is None:
        raise CatalogError("tool_id requires hash8 when version is absent (§1.2)")

    out = f"{parts.namespace}:{parts.name}"
    if parts.version is not None:
        out += f"@{parts.version}"
    if parts.hash8 is not None:
        out += f"#{parts.hash8}"
    if len(out) > _TOOL_ID_MAX_LEN:
        raise CatalogError(f"tool_id exceeds 240-char limit: {len(out)} chars")
    return out


def parse_tool_id(s: str) -> ToolIdParts:
    """Split a canonical ``tool_id`` into its parts (§1.6).

    Args:
        s: A canonical ``tool_id`` string.

    Returns:
        A :class:`ToolIdParts` instance.

    Raises:
        CatalogError: If *s* is malformed, missing the namespace separator,
            longer than 240 characters, or omits ``hash8`` when ``version``
            is also absent.
    """
    if not isinstance(s, str):
        raise CatalogError(f"tool_id must be a string, got {type(s).__name__}")
    if len(s) > _TOOL_ID_MAX_LEN:
        raise CatalogError(f"tool_id exceeds 240-char limit: {len(s)} chars")
    if ":" not in s:
        raise CatalogError(f"tool_id missing namespace separator ':' in {s!r}")

    namespace, rest = s.split(":", 1)

    hash8: str | None = None
    if "#" in rest:
        rest, hash8 = rest.rsplit("#", 1)

    version: str | None = None
    if "@" in rest:
        rest, version = rest.rsplit("@", 1)

    name = rest
    parts = ToolIdParts(namespace=namespace, name=name, version=version, hash8=hash8)
    # Re-run the grammar checks via format_tool_id so both directions agree.
    format_tool_id(parts)
    return parts


def canonical_tool_id(
    *,
    namespace: str,
    name: str,
    upstream_name: str,
    input_schema: dict[str, Any] | None,
    version: str | None = None,
) -> str:
    """Assemble a canonical ``tool_id`` for an upstream MCP tool.

    Convenience wrapper that computes ``hash8`` from *upstream_name* and
    *input_schema* whenever *version* is absent, then calls
    :func:`format_tool_id`.

    Args:
        namespace: Lowercase server-of-origin prefix.
        name: Derived tool name (per §1.4).
        upstream_name: The raw upstream tool name (pre-stripping).  Drives
            ``hash8`` so that two tools sharing an input shape but different
            origins produce distinct ids.
        input_schema: The MCP ``inputSchema``; pass ``None`` if empty.
        version: Optional upstream-declared version.  When provided,
            ``hash8`` is omitted.

    Returns:
        The canonical ``tool_id`` string.
    """
    hash8 = None if version is not None else compute_hash8(upstream_name, input_schema)
    return format_tool_id(ToolIdParts(namespace=namespace, name=name, version=version, hash8=hash8))

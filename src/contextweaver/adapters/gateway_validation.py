"""Untrusted tool-definition and schema hardening for the gateway ingest path.

External MCP servers supply tool definitions and JSON Schemas the gateway does
not control.  Two failure classes are handled here:

* **Malformed tool definitions** (issue #464) — a definition that is not a dict,
  or lacks a non-empty string ``name``, must degrade *that* tool, not abort the
  whole catalog refresh.  :class:`CatalogRefreshReport` records every skip so the
  loss is auditable rather than silent.
* **Untrusted / pathological schemas** (issue #484) — a schema that is not
  well-formed, or whose size / nesting depth / property count is pathological,
  is detected at *ingest* (via :func:`check_schema_health`) instead of at first
  execution.  :func:`build_validator` compiles a reusable ``jsonschema``
  validator so the hot ``tool_execute`` path no longer recompiles per call.

Everything in this module is pure and deterministic — no I/O, no runtime state.
The complexity traversal is iterative on purpose: a recursive walk over a
hostile, deeply-nested schema could exhaust the interpreter stack, which is
exactly the kind of untrusted-input failure this module exists to prevent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast, runtime_checkable

import jsonschema
import jsonschema.exceptions
import jsonschema.validators


@runtime_checkable
class SchemaValidator(Protocol):
    """Minimal structural type for a compiled ``jsonschema`` validator.

    Only the :meth:`validate` method is used by the gateway hot path.  Typing
    against this local Protocol rather than ``jsonschema.protocols.Validator``
    keeps the module import-safe across the whole ``jsonschema>=4.0`` floor and
    avoids coupling to that submodule's availability.
    """

    def validate(self, instance: object) -> None:
        """Validate *instance*, raising ``ValidationError`` on failure."""
        ...


#: Finding categories emitted by :func:`check_schema_health`.
SchemaFindingKind = Literal[
    "not_well_formed",
    "size_exceeded",
    "depth_exceeded",
    "properties_exceeded",
]


@dataclass(frozen=True)
class SchemaLimits:
    """Conservative complexity bounds applied to untrusted tool schemas (#484).

    Defaults are deliberately generous so legitimate large enterprise schemas
    pass; tighten them per deployment when wrapping less-trusted upstreams.

    Attributes:
        max_bytes: Maximum serialized schema size in UTF-8 bytes.
        max_depth: Maximum nesting depth of dict/list containers.
        max_properties: Maximum total ``properties`` keys across the schema.
    """

    max_bytes: int = 65_536
    max_depth: int = 32
    max_properties: int = 512


#: Shared default used when a caller does not supply its own limits.
DEFAULT_SCHEMA_LIMITS = SchemaLimits()


@dataclass(frozen=True)
class SchemaFinding:
    """A single schema-health problem detected at catalog ingest (#484).

    Attributes:
        tool_id: Canonical ``tool_id`` whose schema produced the finding.
        kind: The category of problem (see :data:`SchemaFindingKind`).
        detail: Short, human-readable explanation (already bounded in length).
    """

    tool_id: str
    kind: SchemaFindingKind
    detail: str

    def to_dict(self) -> dict[str, str]:
        """Serialise to a JSON-compatible dict."""
        return {"tool_id": self.tool_id, "kind": self.kind, "detail": self.detail}


@dataclass(frozen=True)
class SkippedTool:
    """A malformed upstream tool definition that was skipped at ingest (#464).

    Attributes:
        index: Position of the definition in the upstream ``tools/list`` array.
        name: The raw ``name`` value if one could be read, else an empty string.
        reason: Short, human-readable reason the definition was skipped.
    """

    index: int
    name: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {"index": self.index, "name": self.name, "reason": self.reason}


@dataclass
class CatalogRefreshReport:
    """Outcome of a catalog refresh: what registered, what was rejected (#464/#484).

    A fully-valid catalog yields ``registered == len(tool_defs)`` with empty
    :attr:`skipped` and :attr:`schema_findings`.  Supports ``int(report)`` so
    callers that only want the registered count keep a terse call site.

    Attributes:
        registered: Number of tools successfully registered.
        skipped: Malformed definitions that were dropped (lenient mode).
        schema_findings: Schema-health problems detected on registered tools.
    """

    registered: int = 0
    skipped: list[SkippedTool] = field(default_factory=list)
    schema_findings: list[SchemaFinding] = field(default_factory=list)

    def __int__(self) -> int:
        """Return the registered-tool count (terse-call-site convenience)."""
        return self.registered

    @property
    def ok(self) -> bool:
        """``True`` when nothing was skipped and no schema findings were raised."""
        return not self.skipped and not self.schema_findings

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "registered": self.registered,
            "skipped": [s.to_dict() for s in self.skipped],
            "schema_findings": [f.to_dict() for f in self.schema_findings],
        }


def _schema_metrics(schema: object) -> tuple[int, int]:
    """Return ``(max_depth, property_count)`` via an iterative traversal.

    Iterative rather than recursive so a hostile, deeply-nested untrusted
    schema cannot exhaust the interpreter stack before the depth bound is
    even checked.

    Args:
        schema: A parsed JSON Schema value (dict, list, or scalar).

    Returns:
        The maximum container-nesting depth and the total number of
        ``properties`` keys found anywhere in the schema.
    """
    max_depth = 0
    property_count = 0
    stack: list[tuple[Any, int]] = [(schema, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > max_depth:
            max_depth = depth
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                property_count += len(properties)
            for value in node.values():
                stack.append((value, depth + 1))
        elif isinstance(node, list):
            for value in node:
                stack.append((value, depth + 1))
    return max_depth, property_count


def check_schema_health(
    tool_id: str,
    schema: dict[str, Any] | None,
    *,
    limits: SchemaLimits = DEFAULT_SCHEMA_LIMITS,
) -> list[SchemaFinding]:
    """Validate an untrusted tool *schema* and bound its complexity (#484).

    Runs the JSON Schema meta-validation (``check_schema``) plus serialized-size,
    nesting-depth, and property-count bounds.  Returns a list of findings; an
    empty list means the schema is well-formed and within bounds.  An empty or
    ``None`` schema is always healthy (the sentinel ``{"type": "object"}`` and
    "no schema" cases impose no validation).

    Args:
        tool_id: Canonical ``tool_id`` the schema belongs to (for the finding).
        schema: The tool's ``inputSchema`` / ``outputSchema`` dict, or ``None``.
        limits: Complexity bounds to enforce.

    Returns:
        A list of :class:`SchemaFinding`, possibly empty.
    """
    findings: list[SchemaFinding] = []
    if not schema:
        return findings

    try:
        serialized = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError) as exc:
        return [SchemaFinding(tool_id, "not_well_formed", f"schema is not serialisable: {exc}")]

    size = len(serialized.encode("utf-8"))
    if size > limits.max_bytes:
        findings.append(
            SchemaFinding(tool_id, "size_exceeded", f"{size} bytes > {limits.max_bytes}")
        )

    depth, property_count = _schema_metrics(schema)
    if depth > limits.max_depth:
        findings.append(
            SchemaFinding(tool_id, "depth_exceeded", f"depth {depth} > {limits.max_depth}")
        )
    if property_count > limits.max_properties:
        findings.append(
            SchemaFinding(
                tool_id,
                "properties_exceeded",
                f"{property_count} properties > {limits.max_properties}",
            )
        )

    try:
        validator_cls = jsonschema.validators.validator_for(schema)
        validator_cls.check_schema(schema)
    except jsonschema.exceptions.SchemaError as exc:
        first_line = str(exc).splitlines()[0] if str(exc) else "invalid schema"
        findings.append(SchemaFinding(tool_id, "not_well_formed", first_line[:200]))

    return findings


def build_validator(schema: dict[str, Any]) -> SchemaValidator:
    """Compile a reusable ``jsonschema`` validator for *schema* (#484).

    The compiled validator is cached by the runtime keyed on ``tool_id`` so the
    hot ``tool_execute`` path validates without recompiling on every call.

    Args:
        schema: A well-formed JSON Schema dict.

    Returns:
        A ready-to-use ``jsonschema`` validator instance.

    Raises:
        jsonschema.exceptions.SchemaError: If *schema* is not well-formed.
    """
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    return cast(SchemaValidator, validator_cls(schema))


__all__ = [
    "DEFAULT_SCHEMA_LIMITS",
    "CatalogRefreshReport",
    "SchemaFinding",
    "SchemaFindingKind",
    "SchemaValidator",
    "SchemaLimits",
    "SkippedTool",
    "build_validator",
    "check_schema_health",
]

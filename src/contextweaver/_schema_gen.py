"""Dataclass → JSON Schema (Draft 2020-12) generator (issue #225).

Stdlib-only.  Inspects ``dataclasses.fields()`` and the type annotations on a
contextweaver dataclass and emits a deterministic JSON Schema document.

Output is byte-stable for the same input — uses ``json.dumps(sort_keys=True,
indent=2)`` and sorts every collection field.  The generator handles the
subset of Python typing used by contextweaver's public envelope types:

- Primitives: ``str``, ``int``, ``float``, ``bool``.
- ``list[T]`` and ``dict[str, T]``.
- ``Literal[a, b, c]`` → ``enum``.
- ``T | None`` / ``Optional[T]`` → ``{"anyOf": [<T>, {"type": "null"}]}``.
- :class:`~enum.Enum` subclasses → enum of member ``value``\\s.
- ``datetime`` → ``{"type": "string", "format": "date-time"}``.
- ``Any`` → ``{}`` (any JSON value).
- Nested dataclasses — inlined as ``$ref`` to a ``$defs`` entry.

Anything outside this subset raises :class:`TypeError` so unsupported field
types fail loud during ``make schemas`` rather than producing a vague /
silently-wrong schema.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import enum
import json
import sys
import types
import typing
from typing import Any, Literal, get_args, get_origin

#: Stable ``$id`` host for published schemas (mkdocs site under ``/schemas/v0/``).
SCHEMA_ID_BASE: str = "https://dgenio.github.io/contextweaver/schemas/v0"

#: Draft URI used in every ``$schema`` field.
JSON_SCHEMA_DRAFT: str = "https://json-schema.org/draft/2020-12/schema"


# ---------------------------------------------------------------------------
# Per-type extras (size bounds, descriptions overrides) — kept here so the
# generator stays dataclass-agnostic.  Issue #225 acceptance criterion:
# ChoiceCard size bounds in docs/gateway_spec.md §2 must round-trip into the
# emitted schema as maxLength / maxItems.
# ---------------------------------------------------------------------------


_FIELD_EXTRAS: dict[str, dict[str, dict[str, Any]]] = {
    "ChoiceCard": {
        "name": {"maxLength": 64},
        "tags": {"maxItems": 5, "items": {"type": "string", "maxLength": 24}},
    },
}


# ---------------------------------------------------------------------------
# Type → schema mapping
# ---------------------------------------------------------------------------


def _schema_for_type(tp: Any, defs: dict[str, Any]) -> dict[str, Any]:  # noqa: ANN401
    """Map a Python type annotation to a JSON Schema fragment.

    Args:
        tp: The runtime type annotation (already resolved by
            :func:`typing.get_type_hints`).
        defs: The ``$defs`` dict.  Nested dataclasses are added in-place.

    Returns:
        A JSON Schema fragment (dict).
    """
    # Any -> permissive
    if tp is Any or tp is object:
        return {}

    # None -> JSON null
    if tp is type(None):
        return {"type": "null"}

    # datetime -> ISO 8601 string per envelope.py serialisation
    if tp is _dt.datetime:
        return {"type": "string", "format": "date-time"}

    # Primitives
    if tp is str:
        return {"type": "string"}
    if tp is int:
        return {"type": "integer"}
    if tp is float:
        return {"type": "number"}
    if tp is bool:
        return {"type": "boolean"}

    # Enum -> enum of member .value's
    if isinstance(tp, type) and issubclass(tp, enum.Enum):
        values = [member.value for member in tp]
        return {"enum": values}

    # Dataclass -> $ref into defs
    if isinstance(tp, type) and dataclasses.is_dataclass(tp):
        name = tp.__name__
        if name not in defs:
            # Insert a sentinel first to break recursion.
            defs[name] = {"$comment": "filling"}
            defs[name] = _dataclass_object_schema(tp, defs)
        return {"$ref": f"#/$defs/{name}"}

    origin = get_origin(tp)
    args = get_args(tp)

    # Literal[a, b, c] -> enum
    if origin is Literal:
        return {"enum": list(args)}

    # Union / Optional
    if origin is typing.Union or _is_union(origin):
        non_none = [a for a in args if a is not type(None)]
        has_none = len(non_none) != len(args)
        sub_schemas = [_schema_for_type(a, defs) for a in non_none]
        if has_none:
            sub_schemas.append({"type": "null"})
        # Optional[T] simplification: anyOf with a single element collapses.
        if len(sub_schemas) == 1:
            return sub_schemas[0]
        return {"anyOf": sub_schemas}

    # list[T]
    if origin in (list, typing.List):  # noqa: UP006 — typing.List for compatibility
        item_tp = args[0] if args else Any
        return {"type": "array", "items": _schema_for_type(item_tp, defs)}

    # tuple[X, Y] — used for trace.scored_children (str, float) pairs.
    # JSON-serialised as either lists of two-elt tuples or as objects; the
    # routing trace's ``scored_children`` is serialised by to_dict() as
    # ``[{"id": str, "score": float}]`` so we surface that form here when the
    # dataclass field is a tuple-of-(str, float).
    if origin in (tuple, typing.Tuple):  # noqa: UP006
        if args == (str, float):
            return {
                "type": "object",
                "properties": {"id": {"type": "string"}, "score": {"type": "number"}},
                "required": ["id", "score"],
                "additionalProperties": False,
            }
        # Generic fallback: positional tuple = fixed-length array.
        return {
            "type": "array",
            "prefixItems": [_schema_for_type(a, defs) for a in args],
            "items": False,
        }

    # dict[str, T]
    if origin in (dict, typing.Dict):  # noqa: UP006
        if not args or args[0] is str:
            value_tp = args[1] if len(args) >= 2 else Any
            return {
                "type": "object",
                "additionalProperties": _schema_for_type(value_tp, defs),
            }
        raise TypeError(f"dict key type must be str, got {args[0]!r}")

    raise TypeError(f"Unsupported type for schema generation: {tp!r}")


def _is_union(origin: Any) -> bool:  # noqa: ANN401 — runtime-introspection helper
    """Return ``True`` for both ``Union[...]`` and ``X | Y`` origins (PEP 604)."""
    if origin is typing.Union:
        return True
    return sys.version_info >= (3, 10) and origin is types.UnionType


# ---------------------------------------------------------------------------
# Dataclass → object schema
# ---------------------------------------------------------------------------


def _dataclass_object_schema(cls: type, defs: dict[str, Any]) -> dict[str, Any]:
    """Convert a dataclass into a JSON Schema ``object`` fragment."""
    hints = typing.get_type_hints(cls)
    properties: dict[str, Any] = {}
    required: list[str] = []
    extras = _FIELD_EXTRAS.get(cls.__name__, {})

    for f in dataclasses.fields(cls):
        if f.name not in hints:
            continue
        schema = _schema_for_type(hints[f.name], defs)
        # Merge per-field extras (size bounds etc.) on top of the inferred schema.
        if f.name in extras:
            # ``items`` override needs to replace, not merge, since lists may
            # specify both type-level (T schema) and bounds-level (items schema).
            for key, value in extras[f.name].items():
                schema[key] = value
        properties[f.name] = schema
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
            required.append(f.name)

    obj: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        obj["required"] = sorted(required)
    docstring = (cls.__doc__ or "").strip().split("\n", 1)[0]
    if docstring:
        obj["description"] = docstring
    return obj


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_schema(cls: type, *, schema_id: str, title: str | None = None) -> dict[str, Any]:
    """Generate a top-level JSON Schema document for a dataclass.

    Args:
        cls: The dataclass to generate a schema for.
        schema_id: Stable ``$id`` URL (e.g.
            ``"https://dgenio.github.io/contextweaver/schemas/v0/build_stats.schema.json"``).
        title: Human-readable title; defaults to ``cls.__name__``.

    Returns:
        A JSON Schema document ready to serialise to disk via
        :func:`schema_to_json`.
    """
    if not dataclasses.is_dataclass(cls):
        raise TypeError(f"generate_schema requires a dataclass, got {cls!r}")
    defs: dict[str, Any] = {}
    object_schema = _dataclass_object_schema(cls, defs)
    doc: dict[str, Any] = {
        "$schema": JSON_SCHEMA_DRAFT,
        "$id": schema_id,
        "title": title or cls.__name__,
        **object_schema,
    }
    # Remove the self-reference from $defs (we inlined it as the top-level).
    defs.pop(cls.__name__, None)
    if defs:
        doc["$defs"] = defs
    return doc


def generate_array_schema(item_cls: type, *, schema_id: str, title: str) -> dict[str, Any]:
    """Generate a schema for ``list[item_cls]`` — used for catalog files."""
    if not dataclasses.is_dataclass(item_cls):
        raise TypeError(f"generate_array_schema requires a dataclass, got {item_cls!r}")
    defs: dict[str, Any] = {}
    item_schema = _dataclass_object_schema(item_cls, defs)
    doc: dict[str, Any] = {
        "$schema": JSON_SCHEMA_DRAFT,
        "$id": schema_id,
        "title": title,
        "type": "array",
        "items": item_schema,
    }
    defs.pop(item_cls.__name__, None)
    if defs:
        doc["$defs"] = defs
    return doc


def schema_to_json(schema: dict[str, Any]) -> str:
    """Serialise a schema to deterministic JSON bytes.

    Trailing newline so the file matches POSIX ``cat``-style conventions and
    ``make schemas-check`` diffs cleanly.
    """
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"

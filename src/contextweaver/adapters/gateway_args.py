"""Opt-in tolerant argument normalization for the gateway execute path (#488).

Models routinely emit tool-call arguments that are *semantically* correct but
*structurally* off: the whole ``args`` object serialized as a JSON string,
``"42"`` for an integer field, ``"true"`` for a boolean.  Under strict schema
validation these hard-fail with ``ARGS_INVALID`` and cost a full model
round-trip to repair.

:func:`normalize_args` is a bounded, **deterministic, rule-based** repair tier —
no fuzzy matching, no key renaming, no dropping of unknown keys.  It is gated
behind ``ProxyRuntime(tolerant_args=True)`` and runs *before* strict validation,
so anything not covered by an explicit rule still fails validation exactly as it
does today.  Every repair is reported so the behaviour stays auditable.

Coercion is applied only when the target schema type *demands* it: a string
value whose field is typed ``string`` is never coerced, and ``"yes"`` is never
turned into a boolean because that is not an exact JSON-boolean literal.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

#: Exact lexical form of a JSON integer (no leading ``+``, no decimal point).
_INTEGER_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)$")
#: Exact lexical form of a JSON number (integer, decimal, or exponent).
_NUMBER_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$")

#: UTF-8 byte-order-mark sometimes prepended to stringified payloads.
_BOM = "﻿"


@dataclass(frozen=True)
class Repair:
    """A single normalization applied to one argument (#488).

    Attributes:
        path: JSON-path-ish location of the repair (``"$"`` for the whole
            object, ``"$.field"`` for a single field).
        rule: The rule that fired — one of ``parse_stringified_object``,
            ``str_to_integer``, ``str_to_number``, ``str_to_boolean``,
            or ``str_to_null``.
    """

    path: str
    rule: str

    def to_dict(self) -> dict[str, str]:
        """Serialise to a JSON-compatible dict."""
        return {"path": self.path, "rule": self.rule}


def _target_types(field_schema: object) -> set[str]:
    """Return the declared JSON Schema ``type`` set for a field, if any."""
    if not isinstance(field_schema, dict):
        return set()
    declared = field_schema.get("type")
    if isinstance(declared, str):
        return {declared}
    if isinstance(declared, list):
        return {t for t in declared if isinstance(t, str)}
    return set()


def _coerce_scalar(value: str, types: set[str]) -> tuple[Any, str | None]:
    """Coerce a string *value* to a scalar demanded by *types*.

    Returns ``(coerced_value, rule_name)`` on success, or ``(value, None)``
    when no rule applies.  Rules are tried in a fixed order; ``string`` in the
    target set means the value is already valid and is left untouched.
    """
    if "string" in types or not types:
        return value, None
    if "integer" in types and _INTEGER_RE.match(value):
        return int(value), "str_to_integer"
    if "number" in types and _NUMBER_RE.match(value):
        number = float(value)
        if math.isfinite(number):
            return number, "str_to_number"
    if "boolean" in types and value in ("true", "false"):
        return value == "true", "str_to_boolean"
    if "null" in types and value == "null":
        return None, "str_to_null"
    return value, None


def normalize_args(
    args: object,
    schema: dict[str, Any] | None,
) -> tuple[Any, list[Repair]]:
    """Deterministically repair common LLM argument malformations (#488).

    Applies, in order:

    1. If *args* is a string that parses as a JSON object, parse it (after
       stripping a leading BOM and surrounding whitespace).
    2. For each field whose schema ``type`` demands a non-string scalar and
       whose value is a string in the exact lexical form of that scalar,
       coerce it.

    No key renaming, no fuzzy matching, no dropping of unknown keys.  Anything
    not repaired is returned unchanged for strict validation to reject.

    Args:
        args: The raw arguments (usually a dict, occasionally a JSON string).
        schema: The hydrated input schema, used to decide which coercions are
            warranted.  ``None`` / empty disables per-field coercion.

    Returns:
        ``(normalized_args, repairs)`` where *repairs* lists every applied rule.
    """
    repairs: list[Repair] = []

    if isinstance(args, str):
        candidate = args.lstrip(_BOM).strip()
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            args = parsed
            repairs.append(Repair("$", "parse_stringified_object"))
        else:
            # Not a JSON object — leave untouched; strict validation will reject.
            return args, repairs

    if not isinstance(args, dict):
        return args, repairs

    properties = (schema or {}).get("properties")
    if not isinstance(properties, dict):
        return args, repairs

    out: dict[str, Any] = dict(args)
    for key, value in list(out.items()):
        if not isinstance(value, str):
            continue
        types = _target_types(properties.get(key))
        coerced, rule = _coerce_scalar(value.strip(), types)
        if rule is not None:
            out[key] = coerced
            repairs.append(Repair(f"$.{key}", rule))

    return out, repairs


__all__ = ["Repair", "normalize_args"]

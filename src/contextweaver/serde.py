"""Serialisation helpers for contextweaver dataclasses.

Provides utilities for converting enums, optional fields, and nested
dataclasses to/from JSON-compatible dicts.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, TypeVar

E = TypeVar("E", bound=Enum)


def enum_to_str(value: Enum) -> str:
    """Return the string value of an enum member."""
    return str(value.value)


def str_to_enum(enum_cls: type[E], value: str) -> E:
    """Parse *value* into an instance of *enum_cls*."""
    return enum_cls(value)


def optional_field(data: dict[str, Any], key: str, default: Any = None) -> Any:
    """Return ``data[key]`` if present and non-``None``, else *default*."""
    value = data.get(key)
    return default if value is None else value


def nest_to_dict(obj: object) -> Any:
    """Recursively serialise a dataclass or collection to a JSON-compatible value."""
    if hasattr(obj, "to_dict"):
        return obj.to_dict()  # type: ignore[union-attr]
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (list, tuple)):
        return [nest_to_dict(item) for item in obj]
    if isinstance(obj, dict):
        return {k: nest_to_dict(v) for k, v in sorted(obj.items())}
    return obj

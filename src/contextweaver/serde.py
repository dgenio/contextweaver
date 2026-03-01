"""Serialisation helpers for contextweaver dataclasses.

Provides utilities for converting enums, optional fields, and nested
dataclasses to/from JSON-compatible dicts.  These helpers are used internally
by the ``to_dict`` / ``from_dict`` methods on all contextweaver dataclasses.
"""

from __future__ import annotations

from enum import Enum
from typing import TypeVar

E = TypeVar("E", bound=Enum)


def enum_to_str(value: Enum) -> str:
    """Return the string value of an enum member.

    Args:
        value: Any :class:`~enum.Enum` instance.

    Returns:
        The ``.value`` of *value* cast to ``str``.
    """
    return str(value.value)


def str_to_enum(enum_cls: type[E], value: str) -> E:
    """Parse *value* into an instance of *enum_cls*.

    Args:
        enum_cls: The target enum class.
        value: Raw string value to parse.

    Returns:
        An instance of *enum_cls*.

    Raises:
        ValueError: If *value* is not a valid member of *enum_cls*.
    """
    return enum_cls(value)


def optional_field(data: dict[str, object], key: str, default: object = None) -> object:
    """Return ``data[key]`` if present and non-``None``, else *default*.

    Args:
        data: Source dict.
        key: Key to look up.
        default: Value to return when the key is absent or ``None``.

    Returns:
        The field value or *default*.
    """
    value = data.get(key)
    return default if value is None else value


def nest_to_dict(obj: object) -> object:
    """Recursively serialise a dataclass or collection to a JSON-compatible value.

    Handles:
    - Objects with a ``to_dict()`` method (contextweaver dataclasses).
    - :class:`~enum.Enum` instances → their ``.value``.
    - ``list`` / ``tuple`` → ``list`` with each element recursed.
    - ``dict`` → ``dict`` with each value recursed (keys are kept as-is).
    - Primitives (``str``, ``int``, ``float``, ``bool``, ``None``) are returned unchanged.

    Args:
        obj: Any value to serialise.

    Returns:
        A JSON-compatible representation of *obj*.
    """
    if hasattr(obj, "to_dict"):
        return obj.to_dict()  # pyright: ignore[reportAttributeAccessIssue]
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, list | tuple):
        return [nest_to_dict(item) for item in obj]
    if isinstance(obj, dict):
        return {k: nest_to_dict(v) for k, v in sorted(obj.items())}
    return obj

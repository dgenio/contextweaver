"""Tests for contextweaver.serde helpers."""

from __future__ import annotations

from enum import Enum

import pytest

from contextweaver.serde import enum_to_str, nest_to_dict, optional_field, str_to_enum
from contextweaver.types import ArtifactRef, ItemKind


class _Color(str, Enum):
    red = "red"
    blue = "blue"


def test_enum_to_str() -> None:
    assert enum_to_str(_Color.red) == "red"
    assert enum_to_str(ItemKind.tool_call) == "tool_call"


def test_str_to_enum() -> None:
    assert str_to_enum(_Color, "blue") is _Color.blue


def test_str_to_enum_invalid() -> None:
    with pytest.raises(ValueError):
        str_to_enum(_Color, "green")


def test_optional_field_present() -> None:
    assert optional_field({"a": 1}, "a") == 1


def test_optional_field_missing() -> None:
    assert optional_field({}, "a", "default") == "default"


def test_optional_field_none() -> None:
    assert optional_field({"a": None}, "a", 42) == 42


def test_nest_to_dict_primitives() -> None:
    assert nest_to_dict("hello") == "hello"
    assert nest_to_dict(42) == 42
    assert nest_to_dict(None) is None


def test_nest_to_dict_enum() -> None:
    assert nest_to_dict(_Color.red) == "red"


def test_nest_to_dict_list() -> None:
    assert nest_to_dict([_Color.red, 1]) == ["red", 1]


def test_nest_to_dict_dict_sorted_keys() -> None:
    result = nest_to_dict({"z": 1, "a": _Color.blue})
    assert result == {"a": "blue", "z": 1}
    assert list(result.keys()) == ["a", "z"]  # type: ignore[union-attr]


def test_nest_to_dict_dataclass() -> None:
    ref = ArtifactRef(handle="h1", media_type="text/plain", size_bytes=10, label="x")
    result = nest_to_dict(ref)
    assert isinstance(result, dict)
    assert result["handle"] == "h1"  # type: ignore[index]

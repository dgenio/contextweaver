"""Tests for contextweaver.adapters.gateway_args (#488).

Covers the deterministic, opt-in argument-normalization rules: stringified
objects, scalar coercions, and the negative cases that must NOT coerce.
"""

from __future__ import annotations

from typing import Any

from contextweaver.adapters.gateway_args import Repair, normalize_args

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "order_id": {"type": "string"},
        "amount": {"type": "number"},
        "count": {"type": "integer"},
        "active": {"type": "boolean"},
        "note": {"type": "string"},
        "maybe": {"type": ["string", "null"]},
        "cleared": {"type": "null"},
    },
}


def _rules(repairs: list[Repair]) -> list[tuple[str, str]]:
    return [(r.path, r.rule) for r in repairs]


# ---------------------------------------------------------------------------
# Rule 1: stringified object
# ---------------------------------------------------------------------------


def test_parses_stringified_object() -> None:
    args, repairs = normalize_args('{"order_id": "4471", "amount": "129.0"}', _SCHEMA)
    # order_id stays a string (schema says string); amount coerces to number.
    assert args == {"order_id": "4471", "amount": 129.0}
    assert ("$", "parse_stringified_object") in _rules(repairs)
    assert ("$.amount", "str_to_number") in _rules(repairs)


def test_strips_bom_and_whitespace_before_parsing() -> None:
    args, repairs = normalize_args('﻿  {"count": "7"}  ', _SCHEMA)
    assert args == {"count": 7}
    assert ("$", "parse_stringified_object") in _rules(repairs)


def test_non_object_string_is_left_untouched() -> None:
    # A bare string that is not a JSON object is not repaired; strict
    # validation downstream is responsible for rejecting it.
    args, repairs = normalize_args('"just a string"', _SCHEMA)
    assert args == '"just a string"'
    assert repairs == []


# ---------------------------------------------------------------------------
# Rule 2: scalar coercions, only when the schema type demands it
# ---------------------------------------------------------------------------


def test_str_to_integer() -> None:
    args, repairs = normalize_args({"count": "42"}, _SCHEMA)
    assert args == {"count": 42}
    assert _rules(repairs) == [("$.count", "str_to_integer")]


def test_str_to_boolean_only_exact_literals() -> None:
    args, repairs = normalize_args({"active": "true"}, _SCHEMA)
    assert args == {"active": True}
    assert _rules(repairs) == [("$.active", "str_to_boolean")]


def test_str_to_null() -> None:
    args, repairs = normalize_args({"cleared": "null"}, _SCHEMA)
    assert args == {"cleared": None}
    assert _rules(repairs) == [("$.cleared", "str_to_null")]


def test_string_or_null_field_leaves_null_literal_untouched() -> None:
    # When "string" is an accepted type, the literal "null" is a valid string
    # and must not be coerced to None.
    args, repairs = normalize_args({"maybe": "null"}, _SCHEMA)
    assert args == {"maybe": "null"}
    assert repairs == []


def test_string_typed_field_is_never_coerced() -> None:
    # order_id is string-typed even though it looks numeric — leave it.
    args, repairs = normalize_args({"order_id": "4471"}, _SCHEMA)
    assert args == {"order_id": "4471"}
    assert repairs == []


# ---------------------------------------------------------------------------
# Negative cases — must NOT coerce
# ---------------------------------------------------------------------------


def test_yes_does_not_coerce_to_boolean() -> None:
    args, repairs = normalize_args({"active": "yes"}, _SCHEMA)
    assert args == {"active": "yes"}
    assert repairs == []


def test_overflowing_exponent_does_not_coerce_to_inf() -> None:
    # "1e999" parses to float('inf'); a non-finite result must be rejected.
    args, repairs = normalize_args({"amount": "1e999"}, _SCHEMA)
    assert args == {"amount": "1e999"}
    assert repairs == []


def test_non_numeric_string_for_integer_field_untouched() -> None:
    args, repairs = normalize_args({"count": "lots"}, _SCHEMA)
    assert args == {"count": "lots"}
    assert repairs == []


def test_already_correct_types_are_untouched() -> None:
    args, repairs = normalize_args({"count": 42, "active": True}, _SCHEMA)
    assert args == {"count": 42, "active": True}
    assert repairs == []


def test_unknown_keys_are_preserved_not_dropped() -> None:
    args, repairs = normalize_args({"surprise": "x", "count": "3"}, _SCHEMA)
    assert args == {"surprise": "x", "count": 3}
    assert _rules(repairs) == [("$.count", "str_to_integer")]


def test_determinism_identical_inputs_identical_repairs() -> None:
    payload = {"count": "3", "amount": "1.5", "active": "false"}
    args1, repairs1 = normalize_args(dict(payload), _SCHEMA)
    args2, repairs2 = normalize_args(dict(payload), _SCHEMA)
    assert args1 == args2
    assert _rules(repairs1) == _rules(repairs2)

"""Tests for contextweaver.routing.selection (issues #515, #479)."""

from __future__ import annotations

import pytest

from contextweaver.exceptions import ConfigError, RouteError
from contextweaver.routing.selection import (
    SELECTION_SCHEMA_PROVIDERS,
    SelectionValidation,
    selection_schema,
    validate_selection,
)

_CANDIDATES = ["github:create_issue@1#a1b2c3d4", "github:list_issues@1#deadbeef"]


# ------------------------------------------------------------------
# selection_schema (#515)
# ------------------------------------------------------------------


def test_selection_schema_json_schema_enum_matches_candidates() -> None:
    schema = selection_schema(_CANDIDATES)
    assert schema["type"] == "object"
    assert schema["required"] == ["tool_id"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["tool_id"] == {"type": "string", "enum": _CANDIDATES}


def test_selection_schema_dedupes_preserving_order() -> None:
    dupes = ["b:two@1#x", "a:one@1#y", "b:two@1#x"]
    assert selection_schema(dupes)["properties"]["tool_id"]["enum"] == ["b:two@1#x", "a:one@1#y"]


def test_selection_schema_custom_property_name() -> None:
    schema = selection_schema(_CANDIDATES, property_name="choice")
    assert "choice" in schema["properties"]
    assert schema["required"] == ["choice"]


def test_selection_schema_openai_envelope() -> None:
    schema = selection_schema(_CANDIDATES, provider="openai", schema_name="pick")
    assert schema["type"] == "json_schema"
    assert schema["json_schema"]["name"] == "pick"
    assert schema["json_schema"]["strict"] is True
    assert schema["json_schema"]["schema"]["properties"]["tool_id"]["enum"] == _CANDIDATES


def test_selection_schema_anthropic_envelope() -> None:
    schema = selection_schema(_CANDIDATES, provider="anthropic", schema_name="pick")
    assert schema["name"] == "pick"
    assert schema["input_schema"]["properties"]["tool_id"]["enum"] == _CANDIDATES


def test_selection_schema_all_providers_supported() -> None:
    for provider in SELECTION_SCHEMA_PROVIDERS:
        assert selection_schema(_CANDIDATES, provider=provider)


def test_selection_schema_empty_candidates_raises() -> None:
    with pytest.raises(RouteError):
        selection_schema([])


def test_selection_schema_unknown_provider_raises() -> None:
    with pytest.raises(ConfigError):
        selection_schema(_CANDIDATES, provider="cohere")


# ------------------------------------------------------------------
# validate_selection (#479)
# ------------------------------------------------------------------


def test_validate_exact_match_accepted() -> None:
    out = validate_selection(_CANDIDATES[0], _CANDIDATES)
    assert out.status == "accepted"
    assert out.selected_id == _CANDIDATES[0]
    assert out.repair is None and out.reason is None
    assert out.ok


def test_validate_strip_repair() -> None:
    out = validate_selection(f"  {_CANDIDATES[0]} ", _CANDIDATES)
    assert out.status == "repaired"
    assert out.repair == "strip"
    assert out.selected_id == _CANDIDATES[0]
    assert out.ok


def test_validate_case_fold_repair() -> None:
    out = validate_selection(_CANDIDATES[0].upper(), _CANDIDATES)
    assert out.status == "repaired"
    assert out.repair == "case_fold"
    assert out.selected_id == _CANDIDATES[0]


def test_validate_unique_prefix_repair() -> None:
    out = validate_selection("github:create_issue", _CANDIDATES)
    assert out.status == "repaired"
    assert out.repair == "prefix"
    assert out.selected_id == _CANDIDATES[0]


def test_validate_ambiguous_prefix_rejected() -> None:
    out = validate_selection("github:", _CANDIDATES)
    assert out.status == "rejected"
    assert out.reason == "ambiguous_prefix"
    assert out.selected_id is None
    assert not out.ok


def test_validate_ambiguous_case_fold_rejected() -> None:
    # Two candidates differing only by case both match under case-folding, and
    # neither is an exact match for the (differently-cased) selection.
    candidates = ["ns:tool@1#aa", "NS:TOOL@1#aa"]
    out = validate_selection("Ns:Tool@1#AA", candidates)
    assert out.status == "rejected"
    assert out.reason == "ambiguous_case_fold"


def test_validate_unknown_rejected() -> None:
    out = validate_selection("does:not_exist@1#zzzz", _CANDIDATES)
    assert out.status == "rejected"
    assert out.reason == "not_a_candidate"


def test_validate_empty_and_none_rejected() -> None:
    for raw in ("", "   ", None):
        out = validate_selection(raw, _CANDIDATES)
        assert out.status == "rejected"
        assert out.reason == "empty_selection"
        assert out.raw_id == (raw or "")


def test_validate_repair_disabled_rejects_near_miss() -> None:
    out = validate_selection(_CANDIDATES[0].upper(), _CANDIDATES, repair=False)
    assert out.status == "rejected"
    assert out.reason == "not_a_candidate"
    # Exact match still accepted with repair disabled.
    assert validate_selection(_CANDIDATES[0], _CANDIDATES, repair=False).status == "accepted"


def test_validate_is_deterministic() -> None:
    a = validate_selection("github:create_issue", _CANDIDATES)
    b = validate_selection("github:create_issue", _CANDIDATES)
    assert a == b


def test_selection_validation_round_trips() -> None:
    out = validate_selection(_CANDIDATES[0].upper(), _CANDIDATES)
    assert SelectionValidation.from_dict(out.to_dict()) == out

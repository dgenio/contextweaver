"""Tests for contextweaver.adapters._framework_common (issue #454).

Directly exercises the shared adapter scaffolding — including the defensive
branches that were previously ``pragma: no cover`` when duplicated inside each
adapter — so a future change to the common helpers is caught here rather than
only through the five per-adapter suites.
"""

from __future__ import annotations

import pytest

from contextweaver.adapters._framework_common import (
    coerce_schema_dict,
    collect_tags,
    infer_namespace,
    require_name_description,
    strip_namespace_prefix,
)
from contextweaver.exceptions import CatalogError

# ---------------------------------------------------------------------------
# infer_namespace
# ---------------------------------------------------------------------------


def test_infer_namespace_dot() -> None:
    assert infer_namespace("calendar.create_event", fallback="x") == "calendar"


def test_infer_namespace_slash() -> None:
    assert infer_namespace("filesystem/read_file", fallback="x") == "filesystem"


def test_infer_namespace_underscore() -> None:
    assert infer_namespace("github_search", fallback="x") == "github"


def test_infer_namespace_empty_uses_fallback() -> None:
    assert infer_namespace("", fallback="fb") == "fb"


def test_infer_namespace_single_segment_uses_fallback() -> None:
    assert infer_namespace("search", fallback="fb") == "fb"


def test_infer_namespace_dot_beats_underscore() -> None:
    # A dot prefix wins over the underscore heuristic.
    assert infer_namespace("a.b_c", fallback="x") == "a"


def test_infer_namespace_unicode_segment() -> None:
    assert infer_namespace("café_search", fallback="x") == "café"


# ---------------------------------------------------------------------------
# strip_namespace_prefix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "ns", "expected"),
    [
        ("github_search", "github", "search"),
        ("calendar.create", "calendar", "create"),
        ("fs/read", "fs", "read"),
        ("search", "github", "search"),  # no prefix → unchanged
        ("github_", "github", "github_"),  # stripping would empty → unchanged
    ],
)
def test_strip_namespace_prefix(name: str, ns: str, expected: str) -> None:
    assert strip_namespace_prefix(name, ns) == expected


# ---------------------------------------------------------------------------
# coerce_schema_dict
# ---------------------------------------------------------------------------


def test_coerce_schema_dict_none() -> None:
    assert coerce_schema_dict(None) == {}


def test_coerce_schema_dict_is_deep_copied() -> None:
    original = {"type": "object", "properties": {"q": {"type": "string"}}}
    result = coerce_schema_dict(original)
    assert result == original
    result["properties"]["q"]["type"] = "integer"
    # Mutating the result must not touch the caller's input.
    assert original["properties"]["q"]["type"] == "string"


def test_coerce_schema_dict_from_model_class() -> None:
    class Model:
        @staticmethod
        def model_json_schema() -> dict[str, object]:
            return {"type": "object", "title": "Model"}

    assert coerce_schema_dict(Model) == {"type": "object", "title": "Model"}


def test_coerce_schema_dict_model_raising_yields_empty() -> None:
    class Bad:
        @staticmethod
        def model_json_schema() -> dict[str, object]:
            raise RuntimeError("boom")

    assert coerce_schema_dict(Bad) == {}


def test_coerce_schema_dict_model_returning_non_dict_yields_empty() -> None:
    class Weird:
        @staticmethod
        def model_json_schema() -> str:
            return "not-a-dict"

    assert coerce_schema_dict(Weird) == {}


def test_coerce_schema_dict_unknown_type_yields_empty() -> None:
    assert coerce_schema_dict(42) == {}


# ---------------------------------------------------------------------------
# collect_tags
# ---------------------------------------------------------------------------


def test_collect_tags_fallback_always_present() -> None:
    assert collect_tags(None, fallback="crewai") == ["crewai"]


def test_collect_tags_merges_and_sorts() -> None:
    assert collect_tags(["write", "calendar"], fallback="agno") == ["agno", "calendar", "write"]


def test_collect_tags_skips_non_string_and_empty() -> None:
    assert collect_tags(["ok", "", 3, None], fallback="fb") == ["fb", "ok"]


def test_collect_tags_accepts_set_and_tuple() -> None:
    assert collect_tags(("b", "a"), fallback="fb") == ["a", "b", "fb"]


# ---------------------------------------------------------------------------
# require_name_description
# ---------------------------------------------------------------------------


def test_require_name_description_ok() -> None:
    assert require_name_description({"name": "n", "description": "d"}, label="X") == ("n", "d")


def test_require_name_description_missing_name() -> None:
    with pytest.raises(CatalogError, match="'name'"):
        require_name_description({"description": "d"}, label="LangChain")


def test_require_name_description_empty_name() -> None:
    with pytest.raises(CatalogError, match="'name'"):
        require_name_description({"name": "", "description": "d"}, label="LangChain")


def test_require_name_description_missing_description() -> None:
    with pytest.raises(CatalogError, match="'description'"):
        require_name_description({"name": "n"}, label="LangChain")


def test_require_name_description_label_in_message() -> None:
    with pytest.raises(CatalogError, match="LangChain"):
        require_name_description({}, label="LangChain")

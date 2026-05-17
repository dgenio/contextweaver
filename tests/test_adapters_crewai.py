"""Tests for contextweaver.adapters.crewai (issue #193)."""

from __future__ import annotations

import pytest

from contextweaver.adapters.crewai import (
    crewai_tool_to_selectable,
    crewai_tools_to_catalog,
    infer_crewai_namespace,
    load_crewai_catalog,
)
from contextweaver.exceptions import CatalogError

# ---------------------------------------------------------------------------
# Namespace inference
# ---------------------------------------------------------------------------


def test_infer_crewai_namespace_underscore() -> None:
    assert infer_crewai_namespace("github_search") == "github"


def test_infer_crewai_namespace_dot() -> None:
    assert infer_crewai_namespace("calendar.create_event") == "calendar"


def test_infer_crewai_namespace_slash() -> None:
    assert infer_crewai_namespace("filesystem/read_file") == "filesystem"


def test_infer_crewai_namespace_empty() -> None:
    assert infer_crewai_namespace("") == "crewai"


def test_infer_crewai_namespace_single_segment() -> None:
    """One-segment names have no detectable namespace; fall back to ``crewai``."""
    assert infer_crewai_namespace("search") == "crewai"


# ---------------------------------------------------------------------------
# Dict → SelectableItem conversion
# ---------------------------------------------------------------------------


def test_crewai_tool_to_selectable_minimal_dict() -> None:
    item = crewai_tool_to_selectable({"name": "search", "description": "Search the corpus."})
    assert item.kind == "tool"
    assert item.id == "crewai:search"
    assert item.name == "search"
    assert item.description == "Search the corpus."
    assert item.namespace == "crewai"
    assert item.tags == ["crewai"]
    assert item.args_schema == {}
    assert item.metadata == {}


def test_crewai_tool_to_selectable_inferred_namespace() -> None:
    item = crewai_tool_to_selectable(
        {"name": "github_search_repos", "description": "Search GitHub repos."}
    )
    assert item.namespace == "github"
    # Namespace prefix stripped from the short name.
    assert item.name == "search_repos"
    assert item.id == "crewai:github_search_repos"
    assert "crewai" in item.tags


def test_crewai_tool_to_selectable_explicit_namespace_overrides_inference() -> None:
    item = crewai_tool_to_selectable(
        {"name": "github_search_repos", "description": "x"},
        namespace="vcs",
    )
    assert item.namespace == "vcs"
    # No "vcs_" prefix on the raw name → full name kept.
    assert item.name == "github_search_repos"


def test_crewai_tool_to_selectable_preserves_user_tags() -> None:
    item = crewai_tool_to_selectable(
        {
            "name": "calendar.create_event",
            "description": "Schedule a meeting.",
            "tags": ["calendar", "write"],
        }
    )
    # ``crewai`` is always added; user-provided tags are merged.
    assert set(item.tags) == {"crewai", "calendar", "write"}


def test_crewai_tool_to_selectable_dict_args_schema_preserved() -> None:
    schema = {"type": "object", "properties": {"q": {"type": "string"}}}
    item = crewai_tool_to_selectable(
        {"name": "search", "description": "Search.", "args_schema": schema}
    )
    assert item.args_schema == schema


def test_crewai_tool_to_selectable_metadata_pass_through() -> None:
    item = crewai_tool_to_selectable(
        {
            "name": "search",
            "description": "Search.",
            "result_as_answer": True,
            "max_usage_count": 5,
        }
    )
    assert item.metadata == {"result_as_answer": True, "max_usage_count": 5}


def test_crewai_tool_to_selectable_missing_name_raises() -> None:
    with pytest.raises(CatalogError, match="'name'"):
        crewai_tool_to_selectable({"description": "missing name"})


def test_crewai_tool_to_selectable_missing_description_raises() -> None:
    with pytest.raises(CatalogError, match="'description'"):
        crewai_tool_to_selectable({"name": "search"})


def test_crewai_tool_to_selectable_empty_name_raises() -> None:
    with pytest.raises(CatalogError, match="'name'"):
        crewai_tool_to_selectable({"name": "", "description": "x"})


# ---------------------------------------------------------------------------
# Batch conversion → Catalog
# ---------------------------------------------------------------------------


def test_crewai_tools_to_catalog_registers_every_item() -> None:
    catalog = crewai_tools_to_catalog(
        [
            {"name": "search", "description": "Search."},
            {"name": "browser.open", "description": "Open a page."},
        ]
    )
    ids = {item.id for item in catalog.all()}
    assert ids == {"crewai:search", "crewai:browser.open"}


def test_crewai_tools_to_catalog_uniform_namespace_override() -> None:
    catalog = crewai_tools_to_catalog(
        [
            {"name": "alpha", "description": "x"},
            {"name": "beta", "description": "y"},
        ],
        namespace="lab",
    )
    namespaces = {item.namespace for item in catalog.all()}
    assert namespaces == {"lab"}


# ---------------------------------------------------------------------------
# Live BaseTool integration (requires the ``crewai`` package)
# ---------------------------------------------------------------------------


def test_load_crewai_catalog_with_real_basetool_instances() -> None:
    crewai_tools = pytest.importorskip("crewai.tools")
    # ``BaseTool`` is imported via ``importorskip`` rather than a top-level
    # import so the file is testable without the ``[crewai]`` extra; the
    # capital local-variable name mirrors the upstream class name and is
    # required by the ``class SearchTool(BaseTool):`` declarations below.
    BaseTool = crewai_tools.BaseTool  # noqa: N806

    class SearchTool(BaseTool):
        name: str = "search"
        description: str = "Search the corpus."

        def _run(self, *args: object, **kwargs: object) -> str:
            return "stub"

    class CalendarTool(BaseTool):
        name: str = "calendar.create_event"
        description: str = "Schedule a meeting."

        def _run(self, *args: object, **kwargs: object) -> str:
            return "stub"

    catalog = load_crewai_catalog([SearchTool(), CalendarTool()])
    ids = {item.id for item in catalog.all()}
    assert ids == {"crewai:search", "crewai:calendar.create_event"}
    # SearchTool has no explicit namespace prefix → fallback to crewai.
    search_item = next(i for i in catalog.all() if i.id == "crewai:search")
    # CrewAI's ``BaseTool.model_dump()`` enriches ``description`` with the
    # tool name + JSON-schema preamble that the framework feeds to the
    # underlying LLM (see ``crewai.tools.base_tool.BaseTool.description``).
    # The adapter is intentionally faithful to that enriched form so the
    # router scores against the same text the LLM eventually sees; assert
    # both the original sentence and the framework's preamble are present.
    assert "Search the corpus." in search_item.description
    assert "Tool Name: search" in search_item.description
    assert search_item.namespace == "crewai"


def test_load_crewai_catalog_rejects_object_without_name_attr() -> None:
    class Bogus:
        description = "no name"

    with pytest.raises(CatalogError, match="'name'"):
        load_crewai_catalog([Bogus()])

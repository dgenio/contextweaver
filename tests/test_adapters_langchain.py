"""Tests for contextweaver.adapters.langchain (issue #502)."""

from __future__ import annotations

import pytest

from contextweaver.adapters.langchain import (
    infer_langchain_namespace,
    langchain_tool_to_selectable,
    langchain_tools_to_catalog,
    load_langchain_catalog,
)
from contextweaver.exceptions import CatalogError

# ---------------------------------------------------------------------------
# Namespace inference
# ---------------------------------------------------------------------------


def test_infer_langchain_namespace_underscore() -> None:
    assert infer_langchain_namespace("tavily_search") == "tavily"


def test_infer_langchain_namespace_single_segment() -> None:
    assert infer_langchain_namespace("search") == "langchain"


# ---------------------------------------------------------------------------
# Dict → SelectableItem
# ---------------------------------------------------------------------------


def test_langchain_tool_to_selectable_minimal() -> None:
    item = langchain_tool_to_selectable({"name": "search", "description": "Search the web."})
    assert item.kind == "tool"
    assert item.id == "langchain:search"
    assert item.name == "search"
    assert item.namespace == "langchain"
    assert item.tags == ["langchain"]
    assert item.args_schema == {}
    assert item.metadata == {}


def test_langchain_tool_to_selectable_inferred_namespace_strips_prefix() -> None:
    item = langchain_tool_to_selectable({"name": "sql_db_query", "description": "Query."})
    assert item.namespace == "sql"
    assert item.name == "db_query"
    assert item.id == "langchain:sql_db_query"


def test_langchain_tool_to_selectable_dict_args_schema_preserved() -> None:
    schema = {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}
    item = langchain_tool_to_selectable(
        {"name": "search", "description": "Search.", "args_schema": schema}
    )
    assert item.args_schema == schema


def test_langchain_tool_to_selectable_args_properties_wrapped() -> None:
    # When only the bare ``args`` properties mapping is present it is wrapped
    # into a minimal object schema.
    item = langchain_tool_to_selectable(
        {"name": "search", "description": "Search.", "args": {"q": {"type": "string"}}}
    )
    assert item.args_schema == {"type": "object", "properties": {"q": {"type": "string"}}}


def test_langchain_tool_to_selectable_args_schema_wins_over_args() -> None:
    item = langchain_tool_to_selectable(
        {
            "name": "search",
            "description": "Search.",
            "args_schema": {"type": "object", "title": "Win"},
            "args": {"q": {"type": "string"}},
        }
    )
    assert item.args_schema == {"type": "object", "title": "Win"}


def test_langchain_tool_to_selectable_return_direct_and_metadata() -> None:
    item = langchain_tool_to_selectable(
        {
            "name": "search",
            "description": "Search.",
            "return_direct": True,
            "metadata": {"source": "tavily"},
        }
    )
    assert item.metadata == {"return_direct": True, "langchain_metadata": {"source": "tavily"}}


def test_langchain_tool_to_selectable_merges_tags() -> None:
    item = langchain_tool_to_selectable(
        {"name": "search", "description": "Search.", "tags": ["web", "read"]}
    )
    assert set(item.tags) == {"langchain", "web", "read"}


def test_langchain_tool_to_selectable_missing_name_raises() -> None:
    with pytest.raises(CatalogError, match="'name'"):
        langchain_tool_to_selectable({"description": "no name"})


def test_langchain_tool_to_selectable_missing_description_raises() -> None:
    with pytest.raises(CatalogError, match="'description'"):
        langchain_tool_to_selectable({"name": "search"})


# ---------------------------------------------------------------------------
# Batch conversion → Catalog
# ---------------------------------------------------------------------------


def test_langchain_tools_to_catalog_registers_every_item() -> None:
    catalog = langchain_tools_to_catalog(
        [
            {"name": "search", "description": "Search."},
            {"name": "browser.open", "description": "Open a page."},
        ]
    )
    ids = {item.id for item in catalog.all()}
    assert ids == {"langchain:search", "langchain:browser.open"}


def test_langchain_tools_to_catalog_namespace_override() -> None:
    catalog = langchain_tools_to_catalog(
        [{"name": "a", "description": "x"}, {"name": "b", "description": "y"}],
        namespace="lab",
    )
    assert {item.namespace for item in catalog.all()} == {"lab"}


# ---------------------------------------------------------------------------
# Duck-typed live loading (no langchain-core required)
# ---------------------------------------------------------------------------


def test_load_langchain_catalog_duck_typed() -> None:
    class FakeTool:
        def __init__(self, name: str, description: str) -> None:
            self.name = name
            self.description = description
            self.args_schema = {"type": "object", "properties": {"q": {"type": "string"}}}
            self.tags = ["web"]
            self.return_direct = False
            self.metadata: dict[str, object] = {}

    catalog = load_langchain_catalog([FakeTool("tavily_search", "Search the web.")])
    item = next(iter(catalog.all()))
    assert item.id == "langchain:tavily_search"
    assert item.namespace == "tavily"
    assert item.args_schema == {"type": "object", "properties": {"q": {"type": "string"}}}
    assert item.metadata == {"return_direct": False}


def test_load_langchain_catalog_rejects_object_without_name() -> None:
    class Bogus:
        description = "no name"

    with pytest.raises(CatalogError, match="'name'"):
        load_langchain_catalog([Bogus()])


def test_load_langchain_catalog_with_real_basetool() -> None:
    tools_mod = pytest.importorskip("langchain_core.tools")
    StructuredTool = tools_mod.StructuredTool  # noqa: N806

    def _add(a: int, b: int) -> int:
        return a + b

    tool = StructuredTool.from_function(func=_add, name="math_add", description="Add two integers.")
    catalog = load_langchain_catalog([tool])
    item = next(iter(catalog.all()))
    assert item.id == "langchain:math_add"
    assert item.namespace == "math"
    # The args schema carries both declared parameters.
    props = item.args_schema.get("properties", {})
    assert "a" in props and "b" in props

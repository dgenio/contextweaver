"""Tests for provider-native tool exporters (issue #609)."""

from __future__ import annotations

import pytest

from contextweaver.exceptions import ConfigError, ItemNotFoundError
from contextweaver.routing.catalog import Catalog
from contextweaver.routing.export import (
    EXPORT_PROVIDERS,
    export_tools,
    to_anthropic_tools,
    to_gemini_function_declarations,
    to_openai_tools,
)
from contextweaver.types import SelectableItem


def _item(
    item_id: str,
    name: str,
    *,
    namespace: str = "",
    schema: dict | None = None,
    description: str = "does a thing",
) -> SelectableItem:
    return SelectableItem(
        id=item_id,
        kind="tool",
        name=name,
        description=description,
        namespace=namespace,
        args_schema=schema or {},
    )


SEARCH_SCHEMA = {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
}


def test_openai_shape() -> None:
    exported = to_openai_tools([_item("t1", "search_docs", schema=SEARCH_SCHEMA)])
    assert exported.provider == "openai"
    (tool,) = exported.tools
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "search_docs"
    assert tool["function"]["description"] == "does a thing"
    assert tool["function"]["parameters"] == SEARCH_SCHEMA


def test_anthropic_shape() -> None:
    exported = to_anthropic_tools([_item("t1", "search_docs", schema=SEARCH_SCHEMA)])
    (tool,) = exported.tools
    assert tool == {
        "name": "search_docs",
        "description": "does a thing",
        "input_schema": SEARCH_SCHEMA,
    }


def test_gemini_shape() -> None:
    exported = to_gemini_function_declarations([_item("t1", "search_docs", schema=SEARCH_SCHEMA)])
    (tool,) = exported.tools
    assert tool == {
        "name": "search_docs",
        "description": "does a thing",
        "parameters": SEARCH_SCHEMA,
    }


def test_unknown_provider_rejected() -> None:
    with pytest.raises(ConfigError):
        export_tools([_item("t1", "x")], provider="mistral")


def test_name_sanitisation_and_resolution() -> None:
    item = _item("fs::read file::v1", "read file!", namespace="fs")
    exported = to_openai_tools([item])
    name = exported.tools[0]["function"]["name"]
    assert name == "read_file_"[: len(name)] or "!" not in name
    import re

    assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name)
    assert exported.resolve(name) == "fs::read file::v1"


def test_cross_namespace_collision_gets_namespace_prefix() -> None:
    a = _item("fs::search", "search", namespace="fs")
    b = _item("web::search", "search", namespace="web")
    exported = to_anthropic_tools([a, b])
    names = [tool["name"] for tool in exported.tools]
    assert names[0] == "search"
    assert names[1] == "web__search"
    assert exported.resolve(names[0]) == "fs::search"
    assert exported.resolve(names[1]) == "web::search"


def test_collision_without_namespace_gets_numeric_suffix() -> None:
    a = _item("t1", "search")
    b = _item("t2", "search")
    exported = to_anthropic_tools([a, b])
    names = [tool["name"] for tool in exported.tools]
    assert names == ["search", "search_2"]


def test_resolve_unknown_name_raises() -> None:
    exported = to_openai_tools([_item("t1", "search")])
    with pytest.raises(ItemNotFoundError):
        exported.resolve("not_exported")


def test_empty_schema_falls_back_to_no_arg_object() -> None:
    exported = to_openai_tools([_item("t1", "ping")])
    params = exported.tools[0]["function"]["parameters"]
    assert params == {"type": "object", "properties": {}}


def test_catalog_hydration_fills_empty_inline_schema() -> None:
    catalog = Catalog()
    catalog.register(_item("t1", "search", schema=SEARCH_SCHEMA))
    shortlist_item = _item("t1", "search")  # schema stripped, as on a card round-trip
    exported = to_openai_tools([shortlist_item], catalog=catalog)
    assert exported.tools[0]["function"]["parameters"] == SEARCH_SCHEMA


def test_inline_schema_wins_over_catalog() -> None:
    catalog = Catalog()
    catalog.register(_item("t1", "search", schema={"type": "object", "properties": {"a": {}}}))
    inline = _item("t1", "search", schema=SEARCH_SCHEMA)
    exported = to_openai_tools([inline], catalog=catalog)
    assert exported.tools[0]["function"]["parameters"] == SEARCH_SCHEMA


def test_ranked_order_preserved_and_deterministic() -> None:
    items = [_item(f"t{i}", f"tool_{i}") for i in range(5)]
    first = export_tools(items, provider="gemini")
    second = export_tools(items, provider="gemini")
    assert first.to_dict() == second.to_dict()
    assert [t["name"] for t in first.tools] == [f"tool_{i}" for i in range(5)]


def test_providers_constant_covers_all_helpers() -> None:
    assert set(EXPORT_PROVIDERS) == {"openai", "anthropic", "gemini"}


def test_to_dict_round_trip_shape() -> None:
    exported = to_openai_tools([_item("t1", "search", schema=SEARCH_SCHEMA)])
    payload = exported.to_dict()
    assert payload["provider"] == "openai"
    assert payload["name_to_tool_id"] == {"search": "t1"}

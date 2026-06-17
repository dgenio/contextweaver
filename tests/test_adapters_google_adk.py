"""Tests for contextweaver.adapters.google_adk (issue #547)."""

from __future__ import annotations

import pytest

from contextweaver.adapters.google_adk import (
    from_google_adk_session,
    google_adk_tool_to_selectable,
    google_adk_tools_to_catalog,
    infer_google_adk_namespace,
    load_google_adk_catalog,
)
from contextweaver.exceptions import CatalogError
from contextweaver.types import ItemKind

# ---------------------------------------------------------------------------
# Tool conversion
# ---------------------------------------------------------------------------


def test_infer_namespace_fallback() -> None:
    assert infer_google_adk_namespace("search") == "google_adk"


def test_tool_to_selectable_minimal() -> None:
    item = google_adk_tool_to_selectable({"name": "search", "description": "Search."})
    assert item.kind == "tool"
    assert item.id == "google_adk:search"
    assert item.namespace == "google_adk"
    assert item.tags == ["google_adk"]
    assert item.metadata == {}


def test_tool_to_selectable_parameters_and_flag() -> None:
    schema = {"type": "object", "properties": {"q": {"type": "string"}}}
    item = google_adk_tool_to_selectable(
        {
            "name": "search_web",
            "description": "Search.",
            "parameters": schema,
            "is_long_running": True,
        }
    )
    assert item.namespace == "search"
    assert item.name == "web"
    assert item.args_schema == schema
    assert item.metadata == {"is_long_running": True}


def test_tool_to_selectable_args_schema_alias() -> None:
    schema = {"type": "object", "title": "Alias"}
    item = google_adk_tool_to_selectable({"name": "t", "description": "d", "args_schema": schema})
    assert item.args_schema == schema


def test_tool_to_selectable_missing_description() -> None:
    with pytest.raises(CatalogError, match="'description'"):
        google_adk_tool_to_selectable({"name": "t"})


def test_tools_to_catalog() -> None:
    catalog = google_adk_tools_to_catalog(
        [{"name": "a", "description": "x"}, {"name": "b", "description": "y"}]
    )
    assert {i.id for i in catalog.all()} == {"google_adk:a", "google_adk:b"}


def test_load_catalog_uses_declaration() -> None:
    class Declaration:
        parameters = {"type": "object", "properties": {"q": {"type": "string"}}}

    class FakeTool:
        name = "maps_geocode"
        description = "Geocode an address."

        def _get_declaration(self) -> Declaration:
            return Declaration()

    catalog = load_google_adk_catalog([FakeTool()])
    item = next(iter(catalog.all()))
    assert item.id == "google_adk:maps_geocode"
    assert item.namespace == "maps"
    assert item.args_schema == {"type": "object", "properties": {"q": {"type": "string"}}}


def test_load_catalog_rejects_object_without_name() -> None:
    class Bogus:
        description = "no name"

    with pytest.raises(CatalogError, match="'name'"):
        load_google_adk_catalog([Bogus()])


# ---------------------------------------------------------------------------
# Session ingestion
# ---------------------------------------------------------------------------


def test_from_session_maps_parts_and_links_parent() -> None:
    items = from_google_adk_session(
        [
            {"author": "user", "content": {"role": "user", "parts": [{"text": "find filings"}]}},
            {
                "author": "model",
                "content": {
                    "role": "model",
                    "parts": [
                        {"function_call": {"id": "x1", "name": "search", "args": {"q": "a"}}}
                    ],
                },
            },
            {
                "author": "user",
                "content": {
                    "role": "user",
                    "parts": [
                        {"function_response": {"id": "x1", "name": "search", "response": {"n": 2}}}
                    ],
                },
            },
        ]
    )
    assert [i.kind for i in items] == [
        ItemKind.user_turn,
        ItemKind.tool_call,
        ItemKind.tool_result,
    ]
    assert items[2].parent_id == items[1].id == "google_adk:tool_call:x1"
    assert items[1].text == '{"q": "a"}'


def test_from_session_model_text_is_agent_msg() -> None:
    items = from_google_adk_session(
        [{"author": "model", "content": {"role": "model", "parts": [{"text": "Here you go."}]}}]
    )
    assert items[0].kind is ItemKind.agent_msg


def test_from_session_skips_blank_text_parts() -> None:
    items = from_google_adk_session(
        [{"author": "user", "content": {"role": "user", "parts": [{"text": "   "}]}}]
    )
    assert items == []


def test_from_session_accepts_session_object() -> None:
    class Session:
        events = [
            {"author": "user", "content": {"role": "user", "parts": [{"text": "hi"}]}},
        ]

    items = from_google_adk_session(Session())
    assert len(items) == 1 and items[0].kind is ItemKind.user_turn


def test_from_session_no_events_raises() -> None:
    with pytest.raises(CatalogError, match="events"):
        from_google_adk_session(object())

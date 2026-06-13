"""Tests for contextweaver.adapters.openai_agents (issue #501)."""

from __future__ import annotations

import pytest

from contextweaver.adapters.openai_agents import (
    from_openai_agents_run,
    infer_openai_agents_namespace,
    load_openai_agents_catalog,
    openai_agents_tool_to_selectable,
    openai_agents_tools_to_catalog,
)
from contextweaver.exceptions import CatalogError
from contextweaver.types import ItemKind

# ---------------------------------------------------------------------------
# Tool conversion
# ---------------------------------------------------------------------------


def test_infer_namespace_fallback() -> None:
    assert infer_openai_agents_namespace("refund") == "openai_agents"


def test_tool_to_selectable_minimal() -> None:
    item = openai_agents_tool_to_selectable({"name": "refund", "description": "Refund an order."})
    assert item.kind == "tool"
    assert item.id == "openai_agents:refund"
    assert item.namespace == "openai_agents"
    assert item.tags == ["openai_agents"]
    assert item.metadata == {}


def test_tool_to_selectable_params_schema_and_strict() -> None:
    schema = {"type": "object", "properties": {"order": {"type": "string"}}, "required": ["order"]}
    item = openai_agents_tool_to_selectable(
        {
            "name": "ops_refund",
            "description": "Refund.",
            "params_json_schema": schema,
            "strict": True,
        }
    )
    assert item.namespace == "ops"
    assert item.name == "refund"
    assert item.args_schema == schema
    assert item.metadata == {"strict": True}


def test_tool_to_selectable_args_schema_alias() -> None:
    schema = {"type": "object", "title": "Alias"}
    item = openai_agents_tool_to_selectable(
        {"name": "t", "description": "d", "args_schema": schema}
    )
    assert item.args_schema == schema


def test_tool_to_selectable_missing_name() -> None:
    with pytest.raises(CatalogError, match="'name'"):
        openai_agents_tool_to_selectable({"description": "x"})


def test_tools_to_catalog() -> None:
    catalog = openai_agents_tools_to_catalog(
        [{"name": "a", "description": "x"}, {"name": "b", "description": "y"}]
    )
    assert {i.id for i in catalog.all()} == {"openai_agents:a", "openai_agents:b"}


def test_load_catalog_duck_typed() -> None:
    class FakeFunctionTool:
        def __init__(self) -> None:
            self.name = "calc_add"
            self.description = "Add numbers."
            self.params_json_schema = {"type": "object", "properties": {"a": {"type": "number"}}}
            self.strict_json_schema = True

    catalog = load_openai_agents_catalog([FakeFunctionTool()])
    item = next(iter(catalog.all()))
    assert item.id == "openai_agents:calc_add"
    assert item.namespace == "calc"
    assert item.metadata == {"strict": True}


def test_load_catalog_rejects_object_without_name() -> None:
    class Bogus:
        description = "no name"

    with pytest.raises(CatalogError, match="'name'"):
        load_openai_agents_catalog([Bogus()])


# ---------------------------------------------------------------------------
# Run ingestion
# ---------------------------------------------------------------------------


def test_from_run_maps_items_and_links_parent() -> None:
    items = from_openai_agents_run(
        [
            {"type": "message_output", "content": "Working on it."},
            {"type": "tool_call", "call_id": "c1", "name": "refund", "arguments": {"order": "1"}},
            {"type": "tool_call_output", "call_id": "c1", "output": {"ok": True}},
        ]
    )
    assert [i.kind for i in items] == [
        ItemKind.agent_msg,
        ItemKind.tool_call,
        ItemKind.tool_result,
    ]
    # The tool result links back to the call for dependency closure.
    assert items[2].parent_id == items[1].id == "openai_agents:tool_call:c1"
    # Args are JSON-encoded with sorted keys.
    assert items[1].text == '{"order": "1"}'
    assert items[1].metadata["tool_name"] == "refund"


def test_from_run_handoff_and_sdk_type_spellings() -> None:
    items = from_openai_agents_run(
        [
            {"type": "tool_call_item", "call_id": "c9", "name": "lookup", "arguments": "{}"},
            {"type": "handoff_output_item", "source": "triage", "target": "billing"},
        ]
    )
    assert items[0].kind is ItemKind.tool_call
    assert items[1].kind is ItemKind.agent_msg
    assert items[1].metadata["handoff"] is True
    assert items[1].metadata["target_agent"] == "billing"


def test_from_run_accepts_run_object_with_new_items() -> None:
    class Run:
        new_items = [{"type": "message_output", "content": "hi"}]

    items = from_openai_agents_run(Run())
    assert len(items) == 1 and items[0].kind is ItemKind.agent_msg


def test_from_run_unknown_type_raises() -> None:
    with pytest.raises(CatalogError, match="unknown type"):
        from_openai_agents_run([{"type": "mystery"}])


def test_from_run_no_items_attr_raises() -> None:
    with pytest.raises(CatalogError, match="new_items"):
        from_openai_agents_run(object())


def test_from_run_skips_known_control_items() -> None:
    # Approval / MCP / compaction control items carry no conversational text
    # and are skipped (not raised on) so ingestion stays robust on real runs.
    items = from_openai_agents_run(
        [
            {"type": "mcp_list_tools_item"},
            {"type": "tool_approval_item", "name": "refund"},
            {"type": "mcp_approval_request_item"},
            {"type": "message_output", "content": "done"},
        ]
    )
    assert [i.kind for i in items] == [ItemKind.agent_msg]
    assert items[0].text == "done"


def test_from_run_message_without_text_falls_back_to_json() -> None:
    items = from_openai_agents_run([{"type": "message_output", "role": "assistant"}])
    assert items[0].kind is ItemKind.agent_msg
    # No readable text → deterministic JSON dump of the payload, never empty.
    assert items[0].text
    assert '"type": "message_output"' in items[0].text


def test_from_run_message_extracts_nested_content_blocks() -> None:
    items = from_openai_agents_run(
        [
            {
                "type": "message_output",
                "content": [{"type": "output_text", "text": "hello "}, {"text": "world"}],
            }
        ]
    )
    assert items[0].text == "hello world"

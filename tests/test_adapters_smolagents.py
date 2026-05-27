"""Tests for contextweaver.adapters.smolagents (issue #274)."""

from __future__ import annotations

import pytest

from contextweaver.adapters.smolagents import (
    from_smolagents_agent,
    infer_smolagents_namespace,
    load_smolagents_catalog,
    selectable_from_smolagents_tool,
    smolagents_tool_to_selectable,
    smolagents_tools_to_catalog,
)
from contextweaver.context.manager import ContextManager
from contextweaver.exceptions import CatalogError
from contextweaver.types import ItemKind

# ---------------------------------------------------------------------------
# Namespace inference
# ---------------------------------------------------------------------------


def test_infer_namespace_underscore() -> None:
    assert infer_smolagents_namespace("web_search") == "web"


def test_infer_namespace_dot() -> None:
    assert infer_smolagents_namespace("hf.image_to_text") == "hf"


def test_infer_namespace_single_segment_falls_back() -> None:
    assert infer_smolagents_namespace("search") == "smolagents"


def test_infer_namespace_empty() -> None:
    assert infer_smolagents_namespace("") == "smolagents"


# ---------------------------------------------------------------------------
# Tool → SelectableItem conversion
# ---------------------------------------------------------------------------


def test_tool_to_selectable_minimal_dict() -> None:
    item = smolagents_tool_to_selectable({"name": "search", "description": "Search."})
    assert item.id == "smolagents:search"
    assert item.kind == "tool"
    assert item.namespace == "smolagents"
    assert item.tags == ["smolagents"]
    assert item.args_schema == {}


def test_tool_to_selectable_inputs_become_json_schema() -> None:
    item = smolagents_tool_to_selectable(
        {
            "name": "web_search",
            "description": "Search.",
            "inputs": {
                "query": {"type": "string", "description": "User query."},
                "top_k": {"type": "integer", "description": "Max results.", "nullable": True},
            },
            "output_type": "string",
        }
    )
    assert item.args_schema["type"] == "object"
    assert item.args_schema["properties"]["query"]["type"] == "string"
    # ``nullable=True`` marks an arg as optional → not in ``required``.
    assert item.args_schema["required"] == ["query"]
    # Output type goes to schema + metadata.
    assert item.args_schema["x-smolagents-output-type"] == "string"
    assert item.metadata["output_type"] == "string"


def test_tool_to_selectable_inputs_required_sorted() -> None:
    item = smolagents_tool_to_selectable(
        {
            "name": "search",
            "description": "Search.",
            "inputs": {
                "b": {"type": "string"},
                "a": {"type": "string"},
            },
        }
    )
    assert item.args_schema["required"] == ["a", "b"]


def test_tool_to_selectable_inputs_string_spec_fallback() -> None:
    item = smolagents_tool_to_selectable(
        {"name": "search", "description": "Search.", "inputs": {"q": "string"}}
    )
    assert item.args_schema["properties"]["q"] == {"type": "string"}
    assert item.args_schema["required"] == ["q"]


def test_tool_to_selectable_explicit_namespace() -> None:
    item = smolagents_tool_to_selectable({"name": "web_search", "description": "x"}, namespace="hf")
    assert item.namespace == "hf"
    assert item.name == "web_search"  # not prefixed with ``hf_`` → kept


def test_tool_to_selectable_missing_name_raises() -> None:
    with pytest.raises(CatalogError, match="'name'"):
        smolagents_tool_to_selectable({"description": "x"})


def test_tool_to_selectable_missing_description_raises() -> None:
    with pytest.raises(CatalogError, match="'description'"):
        smolagents_tool_to_selectable({"name": "search"})


def test_selectable_from_smolagents_tool_alias_matches() -> None:
    assert selectable_from_smolagents_tool is smolagents_tool_to_selectable


# ---------------------------------------------------------------------------
# Batch conversion → Catalog
# ---------------------------------------------------------------------------


def test_tools_to_catalog_registers_every_item() -> None:
    catalog = smolagents_tools_to_catalog(
        [
            {"name": "web_search", "description": "Search."},
            {"name": "image_generator", "description": "Generate."},
        ]
    )
    ids = {item.id for item in catalog.all()}
    assert ids == {"smolagents:web_search", "smolagents:image_generator"}


# ---------------------------------------------------------------------------
# Live SDK integration
# ---------------------------------------------------------------------------


def test_load_smolagents_catalog_with_duck_typed_tool() -> None:
    class FakeTool:
        name = "web_search"
        description = "Search."
        inputs = {"q": {"type": "string"}}
        output_type = "string"

    catalog = load_smolagents_catalog([FakeTool()])
    items = list(catalog.all())
    assert len(items) == 1
    assert items[0].id == "smolagents:web_search"
    assert items[0].args_schema["properties"]["q"] == {"type": "string"}


def test_load_smolagents_catalog_rejects_object_without_name_attr() -> None:
    class Bogus:
        description = "missing"

    with pytest.raises(CatalogError, match="'name'"):
        load_smolagents_catalog([Bogus()])


def test_load_smolagents_catalog_with_real_tool() -> None:
    smolagents = pytest.importorskip("smolagents")
    Tool = smolagents.Tool  # noqa: N806

    class EchoTool(Tool):
        name = "echo"
        description = "Echo back a string."
        inputs = {"text": {"type": "string", "description": "Input."}}
        output_type = "string"

        def forward(self, text: str) -> str:
            return text

    catalog = load_smolagents_catalog([EchoTool()])
    items = list(catalog.all())
    assert len(items) == 1
    assert items[0].id == "smolagents:echo"


# ---------------------------------------------------------------------------
# Step ingestion
# ---------------------------------------------------------------------------


_STEPS: list[dict[str, object]] = [
    {"step_type": "task", "task": "Find latest python release."},
    {
        "step_type": "action",
        "model_output": "Searching the web.",
        "tool_calls": [
            {"id": "c1", "name": "web_search", "arguments": {"query": "python release"}}
        ],
        "observations": "Python 3.13 released 2024-10-07.",
    },
    {"step_type": "final_answer", "final_answer": "Python 3.13.0."},
]


def test_from_steps_decodes_task_action_final() -> None:
    items = from_smolagents_agent(_STEPS)
    kinds = [item.kind for item in items]
    # task → user_turn, reasoning → agent_msg, tool_call, tool_result, final → agent_msg
    assert kinds == [
        ItemKind.user_turn,
        ItemKind.agent_msg,
        ItemKind.tool_call,
        ItemKind.tool_result,
        ItemKind.agent_msg,
    ]


def test_from_steps_tool_result_parent_is_tool_call() -> None:
    items = from_smolagents_agent(_STEPS)
    tool_result = next(i for i in items if i.kind is ItemKind.tool_result)
    tool_call = next(i for i in items if i.kind is ItemKind.tool_call)
    assert tool_result.parent_id == tool_call.id


def test_from_steps_final_answer_metadata() -> None:
    items = from_smolagents_agent(_STEPS)
    final = next(i for i in items if i.metadata.get("final_answer"))
    assert final.kind is ItemKind.agent_msg
    assert final.text == "Python 3.13.0."


def test_from_steps_planning_step_to_plan_state() -> None:
    items = from_smolagents_agent(
        [{"step_type": "planning", "plan": "1. Search\n2. Fetch\n3. Answer"}]
    )
    assert len(items) == 1
    assert items[0].kind is ItemKind.plan_state


def test_from_steps_accepts_agent_with_memory_attribute() -> None:
    class FakeMemory:
        steps = list(_STEPS)

    class FakeAgent:
        memory = FakeMemory()

    items = from_smolagents_agent(FakeAgent())
    assert len(items) > 0
    assert any(i.kind is ItemKind.tool_call for i in items)


def test_from_steps_accepts_agent_with_top_level_steps() -> None:
    class FakeAgent:
        steps = list(_STEPS)

    items = from_smolagents_agent(FakeAgent())
    assert any(i.kind is ItemKind.tool_call for i in items)


def test_from_steps_rejects_object_without_steps() -> None:
    class Bogus:
        pass

    with pytest.raises(CatalogError, match="could not locate"):
        from_smolagents_agent(Bogus())


def test_from_steps_ingests_into_manager() -> None:
    mgr = ContextManager()
    items = from_smolagents_agent(_STEPS, into=mgr)
    log = list(mgr.event_log.all())
    assert [item.id for item in log] == [i.id for i in items]


def test_from_steps_tool_call_args_are_canonical_json() -> None:
    items = from_smolagents_agent(_STEPS)
    tool_call = next(i for i in items if i.kind is ItemKind.tool_call)
    # Canonical sort_keys=True JSON form.
    assert tool_call.text == '{"query": "python release"}'

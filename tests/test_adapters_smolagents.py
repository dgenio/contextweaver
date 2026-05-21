"""Tests for contextweaver.adapters.smolagents + .smolagents_steps (issue #274)."""

from __future__ import annotations

import pytest

from contextweaver.adapters.smolagents import (
    infer_smolagents_namespace,
    load_smolagents_catalog,
    smolagents_tool_to_selectable,
    smolagents_tools_to_catalog,
)
from contextweaver.adapters.smolagents_steps import from_smolagents_agent
from contextweaver.exceptions import CatalogError
from contextweaver.types import ItemKind

# ---------------------------------------------------------------------------
# Namespace inference
# ---------------------------------------------------------------------------


def test_infer_smolagents_namespace_underscore() -> None:
    assert infer_smolagents_namespace("web_search") == "web"


def test_infer_smolagents_namespace_dot() -> None:
    assert infer_smolagents_namespace("hub.image_classification") == "hub"


def test_infer_smolagents_namespace_slash() -> None:
    assert infer_smolagents_namespace("models/llava") == "models"


def test_infer_smolagents_namespace_empty() -> None:
    assert infer_smolagents_namespace("") == "smolagents"


def test_infer_smolagents_namespace_single_segment() -> None:
    assert infer_smolagents_namespace("search") == "smolagents"


# ---------------------------------------------------------------------------
# Dict → SelectableItem conversion
# ---------------------------------------------------------------------------


def test_smolagents_tool_to_selectable_minimal_dict() -> None:
    item = smolagents_tool_to_selectable({"name": "search", "description": "Search the corpus."})
    assert item.kind == "tool"
    assert item.id == "smolagents:search"
    assert item.name == "search"
    assert item.namespace == "smolagents"
    assert item.tags == ["smolagents"]
    assert item.args_schema == {}
    assert item.metadata == {}


def test_smolagents_tool_to_selectable_inputs_become_object_schema() -> None:
    item = smolagents_tool_to_selectable(
        {
            "name": "wikipedia_lookup",
            "description": "Fetch a Wikipedia article.",
            "inputs": {
                "topic": {"type": "string", "description": "Article."},
                "lang": {
                    "type": "string",
                    "description": "Language code.",
                    "nullable": True,
                },
            },
        }
    )
    assert item.args_schema["type"] == "object"
    assert set(item.args_schema["properties"].keys()) == {"topic", "lang"}
    # Required follows non-``nullable`` inputs.
    assert item.args_schema["required"] == ["topic"]


def test_smolagents_tool_to_selectable_output_type_pass_through() -> None:
    item = smolagents_tool_to_selectable(
        {
            "name": "image_classify",
            "description": "Classify an image.",
            "output_type": "string",
        }
    )
    assert item.metadata["output_type"] == "string"


def test_smolagents_tool_to_selectable_explicit_namespace_overrides_inference() -> None:
    item = smolagents_tool_to_selectable(
        {"name": "web_search", "description": "x"},
        namespace="research",
    )
    assert item.namespace == "research"
    assert item.name == "web_search"


def test_smolagents_tool_to_selectable_missing_name_raises() -> None:
    with pytest.raises(CatalogError, match="'name'"):
        smolagents_tool_to_selectable({"description": "missing name"})


def test_smolagents_tool_to_selectable_missing_description_raises() -> None:
    with pytest.raises(CatalogError, match="'description'"):
        smolagents_tool_to_selectable({"name": "search"})


def test_smolagents_tool_to_selectable_empty_inputs_yield_empty_schema() -> None:
    item = smolagents_tool_to_selectable({"name": "noop", "description": "x", "inputs": {}})
    assert item.args_schema == {"type": "object", "properties": {}}


# ---------------------------------------------------------------------------
# Batch conversion → Catalog
# ---------------------------------------------------------------------------


def test_smolagents_tools_to_catalog_registers_every_item() -> None:
    catalog = smolagents_tools_to_catalog(
        [
            {"name": "search", "description": "Search."},
            {"name": "code_interpreter", "description": "Run code."},
        ]
    )
    ids = {item.id for item in catalog.all()}
    assert ids == {"smolagents:search", "smolagents:code_interpreter"}


# ---------------------------------------------------------------------------
# Live load_smolagents_catalog
# ---------------------------------------------------------------------------


class _FakeTool:
    """Minimal duck-typed stand-in for ``smolagents.tools.Tool``."""

    def __init__(self, name: str, description: str, output_type: str = "string") -> None:
        self.name = name
        self.description = description
        self.inputs: dict[str, dict[str, object]] = {"q": {"type": "string"}}
        self.output_type = output_type


class _FakeToolbox:
    def __init__(self, tools: list[_FakeTool]) -> None:
        self.tools = tools


def test_load_smolagents_catalog_accepts_duck_typed_tools() -> None:
    catalog = load_smolagents_catalog(
        [_FakeTool("search", "Search."), _FakeTool("code_interpreter", "Run code.")]
    )
    ids = {item.id for item in catalog.all()}
    assert ids == {"smolagents:search", "smolagents:code_interpreter"}


def test_load_smolagents_catalog_flattens_a_toolbox() -> None:
    toolbox = _FakeToolbox([_FakeTool("alpha", "x"), _FakeTool("beta", "y")])
    catalog = load_smolagents_catalog([toolbox])
    ids = {item.id for item in catalog.all()}
    assert ids == {"smolagents:alpha", "smolagents:beta"}


def test_load_smolagents_catalog_rejects_object_without_name() -> None:
    class Bogus:
        description = "no name"

    with pytest.raises(CatalogError, match="'name'"):
        load_smolagents_catalog([Bogus()])


# ---------------------------------------------------------------------------
# Step-log ingestion
# ---------------------------------------------------------------------------

_STEPS: list[dict[str, object]] = [
    {"task": "Summarise the topic in two sentences."},
    {
        "model_output": "I'll look up the article first.",
        "tool_calls": [
            {
                "id": "call_001",
                "name": "wikipedia_lookup",
                "arguments": {"topic": "Type theory"},
            }
        ],
        "observations": "Type theory is the academic study of type systems...",
    },
    {"final_answer": "Type theory studies type systems."},
]


def test_from_smolagents_agent_decodes_task_thought_call_observation_final() -> None:
    items = from_smolagents_agent(_STEPS)
    kinds = [item.kind for item in items]
    # task → user_turn ; thought → agent_msg ; call ; obs → tool_result ; final → agent_msg
    assert kinds == [
        ItemKind.user_turn,
        ItemKind.agent_msg,
        ItemKind.tool_call,
        ItemKind.tool_result,
        ItemKind.agent_msg,
    ]
    # observation links back to its tool_call
    observation = items[3]
    assert observation.parent_id == "smolagents:tool_call:call_001"
    assert observation.metadata["tool_call_id"] == "call_001"


def test_from_smolagents_agent_rejects_non_list() -> None:
    class NoMemory:
        pass

    with pytest.raises(CatalogError, match="no .memory attribute"):
        from_smolagents_agent(NoMemory())


def test_from_smolagents_agent_accepts_live_memory_object() -> None:
    class FakeStep:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def model_dump(self) -> dict[str, object]:
            return self._payload

    class FakeMemory:
        def __init__(self, steps: list[FakeStep]) -> None:
            self.steps = steps

    class FakeAgent:
        def __init__(self, memory: FakeMemory) -> None:
            self.memory = memory

    agent = FakeAgent(FakeMemory([FakeStep(s) for s in _STEPS]))
    items = from_smolagents_agent(agent)
    assert len(items) == 5


def test_from_smolagents_agent_synthesises_tool_call_id_when_absent() -> None:
    items = from_smolagents_agent(
        [
            {
                "model_output": "thinking",
                "tool_calls": [{"name": "noop", "arguments": {}}],
            }
        ]
    )
    # Synthesised id is ``{step_idx}:{call_idx}``.
    tool_call = items[1]
    assert tool_call.kind == ItemKind.tool_call
    assert tool_call.metadata["tool_call_id"] == "0:0"

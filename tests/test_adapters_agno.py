"""Tests for contextweaver.adapters.agno + .agno_messages (issue #275)."""

from __future__ import annotations

import pytest

from contextweaver.adapters.agno import (
    agno_tool_to_selectable,
    agno_tools_to_catalog,
    infer_agno_namespace,
    load_agno_catalog,
)
from contextweaver.adapters.agno_messages import from_agno_agent
from contextweaver.exceptions import CatalogError
from contextweaver.types import ItemKind

# ---------------------------------------------------------------------------
# Namespace inference
# ---------------------------------------------------------------------------


def test_infer_agno_namespace_underscore() -> None:
    assert infer_agno_namespace("duckduckgo_search") == "duckduckgo"


def test_infer_agno_namespace_dot() -> None:
    assert infer_agno_namespace("calendar.create_event") == "calendar"


def test_infer_agno_namespace_slash() -> None:
    assert infer_agno_namespace("filesystem/read_file") == "filesystem"


def test_infer_agno_namespace_empty() -> None:
    assert infer_agno_namespace("") == "agno"


def test_infer_agno_namespace_single_segment() -> None:
    assert infer_agno_namespace("search") == "agno"


# ---------------------------------------------------------------------------
# Dict → SelectableItem conversion
# ---------------------------------------------------------------------------


def test_agno_tool_to_selectable_minimal_dict() -> None:
    item = agno_tool_to_selectable({"name": "search", "description": "Search."})
    assert item.kind == "tool"
    assert item.id == "agno:search"
    assert item.name == "search"
    assert item.namespace == "agno"
    assert item.tags == ["agno"]
    assert item.args_schema == {}
    assert item.metadata == {}


def test_agno_tool_to_selectable_inferred_namespace() -> None:
    item = agno_tool_to_selectable({"name": "duckduckgo_search", "description": "Search the web."})
    assert item.namespace == "duckduckgo"
    assert item.name == "search"
    assert item.id == "agno:duckduckgo_search"


def test_agno_tool_to_selectable_parameters_pass_through() -> None:
    schema = {
        "type": "object",
        "properties": {"q": {"type": "string"}},
        "required": ["q"],
    }
    item = agno_tool_to_selectable(
        {"name": "search", "description": "Search.", "parameters": schema}
    )
    assert item.args_schema == schema


def test_agno_tool_to_selectable_metadata_flags_pass_through() -> None:
    item = agno_tool_to_selectable(
        {
            "name": "search",
            "description": "Search.",
            "strict": True,
            "show_result": False,
            "stop_after_tool_call": True,
            "toolkit": "SearchTools",
        }
    )
    assert item.metadata == {
        "strict": True,
        "show_result": False,
        "stop_after_tool_call": True,
        "toolkit": "SearchTools",
    }


def test_agno_tool_to_selectable_missing_name_raises() -> None:
    with pytest.raises(CatalogError, match="'name'"):
        agno_tool_to_selectable({"description": "missing name"})


def test_agno_tool_to_selectable_missing_description_raises() -> None:
    with pytest.raises(CatalogError, match="'description'"):
        agno_tool_to_selectable({"name": "search"})


# ---------------------------------------------------------------------------
# Batch conversion → Catalog
# ---------------------------------------------------------------------------


def test_agno_tools_to_catalog_registers_every_item() -> None:
    catalog = agno_tools_to_catalog(
        [
            {"name": "search", "description": "Search."},
            {"name": "calculator_evaluate", "description": "Evaluate math."},
        ]
    )
    ids = {item.id for item in catalog.all()}
    assert ids == {"agno:search", "agno:calculator_evaluate"}


# ---------------------------------------------------------------------------
# Live load_agno_catalog
# ---------------------------------------------------------------------------


class _FakeFunction:
    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description
        self.parameters: dict[str, object] = {"type": "object", "properties": {}}
        self.strict = True
        self.show_result = False
        self.stop_after_tool_call = False


class _FakeToolkit:
    def __init__(self, name: str, functions: dict[str, _FakeFunction]) -> None:
        self.name = name
        self.functions = functions


def test_load_agno_catalog_accepts_duck_typed_functions() -> None:
    catalog = load_agno_catalog(
        [_FakeFunction("search", "Search."), _FakeFunction("write", "Write.")]
    )
    ids = {item.id for item in catalog.all()}
    assert ids == {"agno:search", "agno:write"}


def test_load_agno_catalog_flattens_a_toolkit() -> None:
    toolkit = _FakeToolkit(
        "SearchTools",
        {"web": _FakeFunction("web_search", "x"), "img": _FakeFunction("image_search", "y")},
    )
    catalog = load_agno_catalog([toolkit])
    ids = {item.id for item in catalog.all()}
    assert ids == {"agno:web_search", "agno:image_search"}
    # The toolkit name is stamped on each function's metadata.
    for it in catalog.all():
        assert it.metadata.get("toolkit") == "SearchTools"


def test_load_agno_catalog_rejects_object_without_name() -> None:
    class Bogus:
        description = "no name"

    with pytest.raises(CatalogError, match="'name'"):
        load_agno_catalog([Bogus()])


# ---------------------------------------------------------------------------
# Message ingestion
# ---------------------------------------------------------------------------

_MESSAGES: list[dict[str, object]] = [
    {"role": "system", "content": "You are a research agent."},
    {"role": "user", "content": "Look up NVIDIA."},
    {
        "role": "assistant",
        "content": "Fetching ticker info.",
        "tool_calls": [
            {
                "id": "call_001",
                "type": "function",
                "function": {
                    "name": "yfinance_get_company_info",
                    "arguments": '{"ticker": "NVDA"}',
                },
            }
        ],
    },
    {
        "role": "tool",
        "tool_call_id": "call_001",
        "name": "yfinance_get_company_info",
        "content": "NVIDIA Corporation designs GPUs.",
    },
]


def test_from_agno_agent_decodes_system_user_assistant_toolcall_toolresult() -> None:
    items = from_agno_agent(_MESSAGES)
    kinds = [item.kind for item in items]
    assert kinds == [
        ItemKind.policy,
        ItemKind.user_turn,
        ItemKind.agent_msg,
        ItemKind.tool_call,
        ItemKind.tool_result,
    ]
    tool_result = items[4]
    assert tool_result.parent_id == "agno:tool_call:call_001"
    assert tool_result.metadata["tool_call_id"] == "call_001"


def test_from_agno_agent_accepts_live_agent_object() -> None:
    class FakeMemory:
        def __init__(self, messages: list[dict[str, object]]) -> None:
            self.messages = messages

    class FakeAgent:
        def __init__(self, messages: list[dict[str, object]]) -> None:
            self.memory = FakeMemory(messages)

    items = from_agno_agent(FakeAgent(_MESSAGES))
    assert len(items) == 5


def test_from_agno_agent_rejects_unknown_role() -> None:
    with pytest.raises(CatalogError, match="unknown role"):
        from_agno_agent([{"role": "ghost", "content": "boo"}])


def test_from_agno_agent_rejects_orphan_tool_result() -> None:
    with pytest.raises(CatalogError, match="unknown tool_call_id"):
        from_agno_agent(
            [
                {
                    "role": "tool",
                    "tool_call_id": "missing",
                    "name": "x",
                    "content": "y",
                }
            ]
        )


def test_from_agno_agent_rejects_tool_call_without_id() -> None:
    with pytest.raises(CatalogError, match="missing 'id'"):
        from_agno_agent(
            [
                {
                    "role": "assistant",
                    "content": "x",
                    "tool_calls": [
                        {"type": "function", "function": {"name": "x", "arguments": "{}"}}
                    ],
                }
            ]
        )


def test_from_agno_agent_preserves_reasoning_content_in_assistant_text() -> None:
    items = from_agno_agent(
        [
            {
                "role": "assistant",
                "reasoning_content": "Let me think step by step.",
                "content": "Here is the answer.",
            }
        ]
    )
    assert len(items) == 1
    assert "Let me think step by step." in items[0].text
    assert "Here is the answer." in items[0].text
    assert items[0].metadata.get("has_reasoning") is True


def test_from_agno_agent_rejects_agent_without_memory_or_run_response() -> None:
    class Bare:
        pass

    with pytest.raises(CatalogError, match="neither .run_response.messages"):
        from_agno_agent(Bare())

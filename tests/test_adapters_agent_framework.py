"""Tests for contextweaver.adapters.agent_framework (issue #430)."""

from __future__ import annotations

import sys

import pytest

from contextweaver.adapters.agent_framework import (
    agent_framework_tool_to_selectable,
    agent_framework_tools_to_catalog,
    from_agent_framework_thread,
    infer_agent_framework_namespace,
    load_agent_framework_catalog,
)
from contextweaver.context.manager import ContextManager
from contextweaver.exceptions import CatalogError
from contextweaver.types import ItemKind

# ---------------------------------------------------------------------------
# No optional-SDK leak: importing the adapter must not import ``agent_framework``
# ---------------------------------------------------------------------------


def test_adapter_import_does_not_pull_the_sdk() -> None:
    # Clear both the adapter module and the optional SDK, then re-import the
    # adapter from scratch so the assertion holds regardless of what an earlier
    # test (or a third-party plugin) already imported into the session.
    import importlib

    for name in ("contextweaver.adapters.agent_framework", "agent_framework"):
        sys.modules.pop(name, None)
    importlib.import_module("contextweaver.adapters.agent_framework")
    assert "agent_framework" not in sys.modules


# ---------------------------------------------------------------------------
# Namespace inference
# ---------------------------------------------------------------------------


def test_infer_namespace_underscore() -> None:
    assert infer_agent_framework_namespace("github_create_issue") == "github"


def test_infer_namespace_single_segment() -> None:
    assert infer_agent_framework_namespace("search") == "agent_framework"


# ---------------------------------------------------------------------------
# Tool dict → SelectableItem
# ---------------------------------------------------------------------------


def test_tool_to_selectable_minimal() -> None:
    item = agent_framework_tool_to_selectable({"name": "search", "description": "Search."})
    assert item.kind == "tool"
    assert item.id == "agent_framework:search"
    assert item.name == "search"
    assert item.namespace == "agent_framework"
    assert item.tags == ["agent_framework"]
    assert item.args_schema == {}


def test_tool_to_selectable_strips_inferred_namespace() -> None:
    item = agent_framework_tool_to_selectable({"name": "github.create_issue", "description": "d"})
    assert item.namespace == "github"
    assert item.name == "create_issue"
    assert item.id == "agent_framework:github.create_issue"


def test_tool_to_selectable_parameters_alias_precedence() -> None:
    item = agent_framework_tool_to_selectable(
        {
            "name": "t",
            "description": "d",
            "parameters": {"type": "object", "title": "win"},
            "input_schema": {"type": "object", "title": "lose"},
        }
    )
    assert item.args_schema == {"type": "object", "title": "win"}


def test_tool_to_selectable_input_schema_fallback() -> None:
    item = agent_framework_tool_to_selectable(
        {"name": "t", "description": "d", "input_schema": {"type": "object", "title": "x"}}
    )
    assert item.args_schema == {"type": "object", "title": "x"}


def test_tool_to_selectable_merges_tags() -> None:
    item = agent_framework_tool_to_selectable(
        {"name": "t", "description": "d", "tags": ["read", "github"]}
    )
    assert set(item.tags) == {"agent_framework", "read", "github"}


def test_tool_to_selectable_missing_name_raises() -> None:
    with pytest.raises(CatalogError, match="'name'"):
        agent_framework_tool_to_selectable({"description": "no name"})


def test_tool_to_selectable_missing_description_raises() -> None:
    with pytest.raises(CatalogError, match="'description'"):
        agent_framework_tool_to_selectable({"name": "t"})


# ---------------------------------------------------------------------------
# Batch conversion → Catalog
# ---------------------------------------------------------------------------


def test_tools_to_catalog_registers_every_item() -> None:
    catalog = agent_framework_tools_to_catalog(
        [
            {"name": "a", "description": "x"},
            {"name": "b", "description": "y"},
        ]
    )
    assert {item.id for item in catalog.all()} == {"agent_framework:a", "agent_framework:b"}


def test_tools_to_catalog_namespace_override() -> None:
    catalog = agent_framework_tools_to_catalog([{"name": "a", "description": "x"}], namespace="ms")
    assert next(iter(catalog.all())).namespace == "ms"


# ---------------------------------------------------------------------------
# Duck-typed live loading (no agent-framework SDK required)
# ---------------------------------------------------------------------------


def test_load_agent_framework_catalog_duck_typed() -> None:
    class FakeFunction:
        def __init__(self) -> None:
            self.name = "weather_lookup"
            self.description = "Look up the weather."
            self.parameters = {"type": "object", "properties": {"city": {"type": "string"}}}
            self.tags = ["weather"]

    catalog = load_agent_framework_catalog([FakeFunction()])
    item = next(iter(catalog.all()))
    assert item.id == "agent_framework:weather_lookup"
    assert item.namespace == "weather"
    assert item.args_schema == {"type": "object", "properties": {"city": {"type": "string"}}}


def test_load_agent_framework_catalog_empty_parameters_not_overridden() -> None:
    # An explicit empty ``parameters`` schema must win over ``input_schema``;
    # the old ``parameters or input_schema`` fallback wrongly discarded it.
    class FakeFunction:
        def __init__(self) -> None:
            self.name = "noop"
            self.description = "Takes no arguments."
            self.parameters: dict[str, object] = {}
            self.input_schema = {"type": "object", "properties": {"x": {"type": "string"}}}

    catalog = load_agent_framework_catalog([FakeFunction()])
    item = next(iter(catalog.all()))
    assert item.args_schema == {}


def test_load_agent_framework_catalog_input_schema_used_when_parameters_absent() -> None:
    class FakeFunction:
        def __init__(self) -> None:
            self.name = "lookup"
            self.description = "Look something up."
            self.parameters = None
            self.input_schema = {"type": "object", "properties": {"q": {"type": "string"}}}

    catalog = load_agent_framework_catalog([FakeFunction()])
    item = next(iter(catalog.all()))
    assert item.args_schema == {"type": "object", "properties": {"q": {"type": "string"}}}


def test_load_agent_framework_catalog_rejects_object_without_name() -> None:
    class Bogus:
        description = "no name"

    with pytest.raises(CatalogError, match="'name'"):
        load_agent_framework_catalog([Bogus()])


# ---------------------------------------------------------------------------
# Thread ingestion → ContextItems
# ---------------------------------------------------------------------------


def test_from_thread_text_roles() -> None:
    thread = [
        {"role": "user", "contents": [{"text": "hello"}]},
        {"role": "assistant", "contents": [{"text": "hi there"}]},
    ]
    items = from_agent_framework_thread(thread)
    assert [i.kind for i in items] == [ItemKind.user_turn, ItemKind.agent_msg]
    assert items[0].text == "hello"


def test_from_thread_function_call_and_result_parentage() -> None:
    thread = [
        {
            "role": "assistant",
            "contents": [
                {"name": "get_weather", "call_id": "c1", "arguments": {"city": "Paris"}},
            ],
        },
        {
            "role": "tool",
            "contents": [{"call_id": "c1", "result": {"temp": 21}}],
        },
    ]
    items = from_agent_framework_thread(thread)
    call, result = items
    assert call.kind == ItemKind.tool_call
    assert call.id == "agent_framework:tool_call:c1"
    assert call.text == '{"city": "Paris"}'
    assert result.kind == ItemKind.tool_result
    assert result.parent_id == "agent_framework:tool_call:c1"
    assert result.text == '{"temp": 21}'


def test_from_thread_bare_text_message() -> None:
    items = from_agent_framework_thread([{"role": "user", "text": "bare"}])
    assert len(items) == 1
    assert items[0].text == "bare"


def test_from_thread_role_enum_value_coerced() -> None:
    class Role:
        value = "user"

    items = from_agent_framework_thread([{"role": Role(), "contents": [{"text": "x"}]}])
    assert items[0].kind == ItemKind.user_turn


def test_from_thread_via_thread_messages_attribute() -> None:
    class Thread:
        messages = [{"role": "user", "contents": [{"text": "x"}]}]

    assert len(from_agent_framework_thread(Thread())) == 1


def test_from_thread_ingests_into_manager() -> None:
    mgr = ContextManager()
    from_agent_framework_thread([{"role": "user", "contents": [{"text": "hi"}]}], into=mgr)
    assert any(item.text == "hi" for item in mgr.event_log.all())


def test_from_thread_non_dict_message_raises() -> None:
    with pytest.raises(CatalogError, match="not a dict-like object"):
        from_agent_framework_thread([42])


def test_from_thread_missing_messages_raises() -> None:
    with pytest.raises(CatalogError, match="could not locate a 'messages' iterable"):
        from_agent_framework_thread(object())


# ---------------------------------------------------------------------------
# Live SDK path (skips cleanly when the extra is not installed)
# ---------------------------------------------------------------------------


def test_load_with_real_sdk() -> None:
    af = pytest.importorskip("agent_framework")
    ai_function = getattr(af, "ai_function", None)
    if ai_function is None:  # pragma: no cover - depends on installed SDK surface
        pytest.skip("agent_framework.ai_function not available in this version")

    @ai_function
    def sample(city: str) -> str:
        """Look up the weather for a city."""
        return city

    catalog = load_agent_framework_catalog([sample])
    assert len(catalog.all()) == 1

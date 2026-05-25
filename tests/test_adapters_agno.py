"""Tests for contextweaver.adapters.agno (issue #275)."""

from __future__ import annotations

import pytest

from contextweaver.adapters.agno import (
    agno_tool_to_selectable,
    agno_tools_to_catalog,
    from_agno_agent,
    from_agno_session,
    infer_agno_namespace,
    load_agno_catalog,
    selectable_from_agno_tool,
)
from contextweaver.context.manager import ContextManager
from contextweaver.exceptions import CatalogError
from contextweaver.types import ItemKind

# ---------------------------------------------------------------------------
# Namespace inference
# ---------------------------------------------------------------------------


def test_infer_namespace_underscore() -> None:
    assert infer_agno_namespace("duckduckgo_search") == "duckduckgo"


def test_infer_namespace_dot() -> None:
    assert infer_agno_namespace("yfinance.get_info") == "yfinance"


def test_infer_namespace_empty() -> None:
    assert infer_agno_namespace("") == "agno"


def test_infer_namespace_single_segment() -> None:
    assert infer_agno_namespace("calculator") == "agno"


# ---------------------------------------------------------------------------
# Tool → SelectableItem conversion
# ---------------------------------------------------------------------------


def test_tool_to_selectable_minimal_dict() -> None:
    item = agno_tool_to_selectable({"name": "search", "description": "Search."})
    assert item.id == "agno:search"
    assert item.kind == "tool"
    assert item.namespace == "agno"
    assert item.tags == ["agno"]


def test_tool_to_selectable_parameters_preserved() -> None:
    schema = {
        "type": "object",
        "properties": {"q": {"type": "string"}},
        "required": ["q"],
    }
    item = agno_tool_to_selectable(
        {"name": "search", "description": "Search.", "parameters": schema}
    )
    assert item.args_schema == schema


def test_tool_to_selectable_toolkit_name_becomes_namespace() -> None:
    item = agno_tool_to_selectable(
        {
            "name": "duckduckgo_search",
            "description": "Search.",
            "toolkit_name": "duckduckgo",
        }
    )
    assert item.namespace == "duckduckgo"
    # The name had the namespace prefix → it gets stripped.
    assert item.name == "search"
    assert item.metadata["toolkit_name"] == "duckduckgo"


def test_tool_to_selectable_explicit_namespace_wins_over_toolkit() -> None:
    item = agno_tool_to_selectable(
        {
            "name": "duckduckgo_search",
            "description": "Search.",
            "toolkit_name": "duckduckgo",
        },
        namespace="search",
    )
    assert item.namespace == "search"


def test_tool_to_selectable_args_schema_alias_accepted() -> None:
    schema = {"type": "object", "properties": {"q": {"type": "string"}}}
    item = agno_tool_to_selectable(
        {"name": "search", "description": "Search.", "args_schema": schema}
    )
    assert item.args_schema == schema


def test_tool_to_selectable_metadata_pass_through() -> None:
    item = agno_tool_to_selectable(
        {
            "name": "search",
            "description": "Search.",
            "strict": True,
            "show_result": False,
            "requires_confirmation": True,
        }
    )
    assert item.metadata["strict"] is True
    assert item.metadata["show_result"] is False
    assert item.metadata["requires_confirmation"] is True


def test_tool_to_selectable_missing_name_raises() -> None:
    with pytest.raises(CatalogError, match="'name'"):
        agno_tool_to_selectable({"description": "x"})


def test_tool_to_selectable_missing_description_raises() -> None:
    with pytest.raises(CatalogError, match="'description'"):
        agno_tool_to_selectable({"name": "x"})


def test_selectable_from_agno_tool_alias_matches() -> None:
    assert selectable_from_agno_tool is agno_tool_to_selectable


# ---------------------------------------------------------------------------
# Batch conversion → Catalog
# ---------------------------------------------------------------------------


def test_tools_to_catalog_registers_every_item() -> None:
    catalog = agno_tools_to_catalog(
        [
            {"name": "search", "description": "S."},
            {"name": "fetch", "description": "F."},
        ]
    )
    assert {it.id for it in catalog.all()} == {"agno:search", "agno:fetch"}


# ---------------------------------------------------------------------------
# Toolkit / Function loading
# ---------------------------------------------------------------------------


def test_load_catalog_with_duck_typed_function() -> None:
    class FakeFn:
        name = "duckduckgo_search"
        description = "Search the web via DuckDuckGo."
        parameters = {"type": "object", "properties": {"q": {"type": "string"}}}

    catalog = load_agno_catalog([FakeFn()])
    items = list(catalog.all())
    assert len(items) == 1
    assert items[0].id == "agno:duckduckgo_search"
    assert items[0].namespace == "duckduckgo"


def test_load_catalog_walks_toolkit_functions_dict() -> None:
    class FakeFn:
        def __init__(self, name: str) -> None:
            self.name = name
            self.description = f"Function {name}"
            self.parameters: dict[str, object] = {"type": "object", "properties": {}}

    class FakeToolkit:
        name = "duckduckgo"
        functions = {"search": FakeFn("search"), "news": FakeFn("news")}

    catalog = load_agno_catalog([FakeToolkit()])
    items = sorted(catalog.all(), key=lambda i: i.id)
    assert [it.id for it in items] == ["agno:news", "agno:search"]
    assert {it.metadata.get("toolkit_name") for it in items} == {"duckduckgo"}
    # Toolkit name drives the namespace per #275.
    assert {it.namespace for it in items} == {"duckduckgo"}


def test_load_catalog_accepts_bare_callable_with_docstring() -> None:
    def search(q: str) -> str:
        """Look something up."""
        return f"results for {q}"

    catalog = load_agno_catalog([search])
    items = list(catalog.all())
    assert items[0].id == "agno:search"
    assert "Look something up" in items[0].description


def test_load_catalog_rejects_object_without_name() -> None:
    class Bogus:
        description = "no name"

    with pytest.raises(CatalogError, match="'name'"):
        load_agno_catalog([Bogus()])


def test_load_catalog_rejects_object_without_description() -> None:
    class Bogus:
        name = "x"

    with pytest.raises(CatalogError, match="'description'"):
        load_agno_catalog([Bogus()])


def test_load_catalog_with_real_agno_function() -> None:
    agno_tools = pytest.importorskip("agno.tools")
    # Agno exposes a ``Function`` model — duck-type if the class name differs.
    Function = getattr(agno_tools, "Function", None)  # noqa: N806
    if Function is None:  # pragma: no cover - upstream rename guard
        pytest.skip("agno.tools.Function not available")
    fn = Function(name="echo", description="Echo a string.", parameters={"type": "object"})
    catalog = load_agno_catalog([fn])
    assert any(it.id == "agno:echo" for it in catalog.all())


# ---------------------------------------------------------------------------
# Session ingestion
# ---------------------------------------------------------------------------


_SESSION: list[dict[str, object]] = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Tell me about NVDA."},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "yfinance_get_company_info",
                    "arguments": '{"ticker": "NVDA"}',
                },
            },
        ],
    },
    {
        "role": "tool",
        "tool_call_id": "call-1",
        "name": "yfinance_get_company_info",
        "content": "NVIDIA Corp · semis · CEO Jensen Huang",
    },
    {"role": "assistant", "content": "NVIDIA Corp is a semiconductor company."},
]


def test_from_session_decodes_each_role() -> None:
    items = from_agno_session(_SESSION)
    kinds = [item.kind for item in items]
    assert kinds == [
        ItemKind.policy,
        ItemKind.user_turn,
        ItemKind.tool_call,
        ItemKind.tool_result,
        ItemKind.agent_msg,
    ]


def test_from_session_links_tool_result_to_tool_call() -> None:
    items = from_agno_session(_SESSION)
    tool_result = next(i for i in items if i.kind is ItemKind.tool_result)
    assert tool_result.parent_id == "agno:tool_call:call-1"


def test_from_session_ingests_into_manager() -> None:
    mgr = ContextManager()
    items = from_agno_session(_SESSION, into=mgr)
    log = list(mgr.event_log.all())
    assert [item.id for item in log] == [i.id for i in items]


def test_from_session_rejects_orphan_tool_message() -> None:
    bad = [
        {
            "role": "tool",
            "tool_call_id": "nope",
            "name": "x",
            "content": "y",
        },
    ]
    with pytest.raises(CatalogError, match="unknown tool_call_id"):
        from_agno_session(bad)


def test_from_session_rejects_unknown_role() -> None:
    with pytest.raises(CatalogError, match="unknown role"):
        from_agno_session([{"role": "judge", "content": "x"}])


def test_from_session_rejects_tool_call_without_id() -> None:
    bad = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"type": "function", "function": {"name": "x"}}],
        }
    ]
    with pytest.raises(CatalogError, match="missing 'id'"):
        from_agno_session(bad)


def test_from_session_walks_session_messages_attr() -> None:
    class FakeSession:
        messages = list(_SESSION)

    items = from_agno_session(FakeSession())
    assert any(i.kind is ItemKind.tool_call for i in items)


def test_from_session_walks_runs_list() -> None:
    class FakeRun:
        messages = list(_SESSION)

    class FakeSession:
        runs = [FakeRun()]

    items = from_agno_session(FakeSession())
    assert any(i.kind is ItemKind.tool_call for i in items)


def test_from_agno_agent_alias_matches() -> None:
    assert from_agno_agent is from_agno_session


def test_from_session_rejects_object_without_messages_or_runs() -> None:
    class Bogus:
        pass

    with pytest.raises(CatalogError, match="could not locate"):
        from_agno_session(Bogus())

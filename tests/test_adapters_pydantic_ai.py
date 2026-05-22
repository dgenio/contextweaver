"""Tests for contextweaver.adapters.pydantic_ai (issue #272)."""

from __future__ import annotations

import pytest

from contextweaver.adapters.pydantic_ai import (
    from_pydantic_ai_messages,
    infer_pydantic_ai_namespace,
    load_pydantic_ai_catalog,
    pydantic_ai_tool_to_selectable,
    pydantic_ai_tools_to_catalog,
    selectable_from_pydantic_tool,
    to_pydantic_ai_messages,
)
from contextweaver.context.manager import ContextManager
from contextweaver.exceptions import CatalogError
from contextweaver.types import ItemKind

# ---------------------------------------------------------------------------
# Namespace inference
# ---------------------------------------------------------------------------


def test_infer_namespace_underscore() -> None:
    assert infer_pydantic_ai_namespace("github_search") == "github"


def test_infer_namespace_dot() -> None:
    assert infer_pydantic_ai_namespace("calendar.create_event") == "calendar"


def test_infer_namespace_slash() -> None:
    assert infer_pydantic_ai_namespace("filesystem/read_file") == "filesystem"


def test_infer_namespace_empty() -> None:
    assert infer_pydantic_ai_namespace("") == "pydantic_ai"


def test_infer_namespace_single_segment() -> None:
    assert infer_pydantic_ai_namespace("search") == "pydantic_ai"


# ---------------------------------------------------------------------------
# Dict → SelectableItem conversion
# ---------------------------------------------------------------------------


def test_tool_to_selectable_minimal_dict() -> None:
    item = pydantic_ai_tool_to_selectable({"name": "search", "description": "Search the corpus."})
    assert item.kind == "tool"
    assert item.id == "pydantic_ai:search"
    assert item.name == "search"
    assert item.namespace == "pydantic_ai"
    assert item.tags == ["pydantic_ai"]
    assert item.args_schema == {}
    assert item.metadata == {}


def test_tool_to_selectable_inferred_namespace() -> None:
    item = pydantic_ai_tool_to_selectable(
        {"name": "github_search_repos", "description": "Search GitHub repos."}
    )
    assert item.namespace == "github"
    assert item.name == "search_repos"
    assert item.id == "pydantic_ai:github_search_repos"


def test_tool_to_selectable_explicit_namespace() -> None:
    item = pydantic_ai_tool_to_selectable(
        {"name": "github_search_repos", "description": "x"},
        namespace="vcs",
    )
    assert item.namespace == "vcs"
    assert item.name == "github_search_repos"


def test_tool_to_selectable_parameters_json_schema() -> None:
    schema = {
        "type": "object",
        "properties": {"q": {"type": "string"}},
        "required": ["q"],
    }
    item = pydantic_ai_tool_to_selectable(
        {
            "name": "search",
            "description": "Search.",
            "parameters_json_schema": schema,
        }
    )
    assert item.args_schema == schema


def test_tool_to_selectable_args_schema_alias() -> None:
    """``args_schema`` is accepted for symmetry with the CrewAI adapter."""
    schema = {"type": "object", "properties": {"q": {"type": "string"}}}
    item = pydantic_ai_tool_to_selectable(
        {"name": "search", "description": "Search.", "args_schema": schema}
    )
    assert item.args_schema == schema


def test_tool_to_selectable_metadata_pass_through() -> None:
    item = pydantic_ai_tool_to_selectable(
        {
            "name": "search",
            "description": "Search.",
            "takes_ctx": True,
            "max_retries": 3,
            "strict": True,
        }
    )
    assert item.metadata == {"takes_ctx": True, "max_retries": 3, "strict": True}


def test_tool_to_selectable_missing_name_raises() -> None:
    with pytest.raises(CatalogError, match="'name'"):
        pydantic_ai_tool_to_selectable({"description": "x"})


def test_tool_to_selectable_missing_description_raises() -> None:
    with pytest.raises(CatalogError, match="'description'"):
        pydantic_ai_tool_to_selectable({"name": "search"})


def test_selectable_from_pydantic_tool_alias_matches() -> None:
    """The #272 spelling ``selectable_from_pydantic_tool`` is an alias."""
    assert selectable_from_pydantic_tool is pydantic_ai_tool_to_selectable


# ---------------------------------------------------------------------------
# Batch conversion → Catalog
# ---------------------------------------------------------------------------


def test_tools_to_catalog_registers_every_item() -> None:
    catalog = pydantic_ai_tools_to_catalog(
        [
            {"name": "search", "description": "Search."},
            {"name": "browser.open", "description": "Open a page."},
        ]
    )
    ids = {item.id for item in catalog.all()}
    assert ids == {"pydantic_ai:search", "pydantic_ai:browser.open"}


def test_tools_to_catalog_uniform_namespace_override() -> None:
    catalog = pydantic_ai_tools_to_catalog(
        [{"name": "alpha", "description": "x"}, {"name": "beta", "description": "y"}],
        namespace="lab",
    )
    assert {item.namespace for item in catalog.all()} == {"lab"}


# ---------------------------------------------------------------------------
# Live SDK integration
# ---------------------------------------------------------------------------


def test_load_pydantic_ai_catalog_rejects_object_without_name_attr() -> None:
    class Bogus:
        description = "no name"

    with pytest.raises(CatalogError, match="'name'"):
        load_pydantic_ai_catalog([Bogus()])


def test_load_pydantic_ai_catalog_with_duck_typed_tool() -> None:
    """Bare objects with ``name`` / ``description`` are accepted."""

    class FakeTool:
        name = "weather_get_forecast"
        description = "Get a forecast."
        parameters_json_schema = {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        }
        tags: list[str] = ["weather"]

    catalog = load_pydantic_ai_catalog([FakeTool()])
    items = list(catalog.all())
    assert len(items) == 1
    assert items[0].id == "pydantic_ai:weather_get_forecast"
    assert items[0].namespace == "weather"
    assert items[0].args_schema["required"] == ["city"]


def test_load_pydantic_ai_catalog_with_real_pydantic_ai_tool() -> None:
    pydantic_ai = pytest.importorskip("pydantic_ai")
    # Pydantic AI ships a ``Tool`` class in ``pydantic_ai.tools``.
    Tool = pydantic_ai.Tool  # noqa: N806

    def weather(city: str) -> str:
        """Get a forecast for *city*."""
        return f"sunny in {city}"

    tool = Tool(weather)
    catalog = load_pydantic_ai_catalog([tool])
    items = list(catalog.all())
    assert len(items) == 1
    assert items[0].id.startswith("pydantic_ai:")
    assert "weather" in items[0].id


# ---------------------------------------------------------------------------
# Message round-trip
# ---------------------------------------------------------------------------


_TRANSCRIPT: list[dict[str, object]] = [
    {
        "kind": "request",
        "parts": [
            {"part_kind": "system-prompt", "content": "You are helpful."},
            {"part_kind": "user-prompt", "content": "Forecast for Lisbon?"},
        ],
    },
    {
        "kind": "response",
        "parts": [
            {
                "part_kind": "tool-call",
                "tool_name": "weather_get_forecast",
                "tool_call_id": "call-001",
                "args": {"city": "Lisbon"},
            },
        ],
    },
    {
        "kind": "request",
        "parts": [
            {
                "part_kind": "tool-return",
                "tool_name": "weather_get_forecast",
                "tool_call_id": "call-001",
                "content": "23 C, partly cloudy",
            },
        ],
    },
    {
        "kind": "response",
        "parts": [
            {"part_kind": "text", "content": "It's 23 C in Lisbon."},
        ],
    },
]


def test_from_messages_decodes_request_parts() -> None:
    items = from_pydantic_ai_messages(_TRANSCRIPT)
    kinds = [item.kind for item in items]
    assert kinds == [
        ItemKind.policy,
        ItemKind.user_turn,
        ItemKind.tool_call,
        ItemKind.tool_result,
        ItemKind.agent_msg,
    ]


def test_from_messages_links_tool_result_to_tool_call() -> None:
    items = from_pydantic_ai_messages(_TRANSCRIPT)
    tool_result = next(i for i in items if i.kind is ItemKind.tool_result)
    assert tool_result.parent_id == "pydantic_ai:tool_call:call-001"
    assert tool_result.metadata["tool_call_id"] == "call-001"


def test_from_messages_ingests_into_manager() -> None:
    mgr = ContextManager()
    items = from_pydantic_ai_messages(_TRANSCRIPT, into=mgr)
    log = list(mgr.event_log.all())
    assert [item.id for item in log] == [i.id for i in items]


def test_from_messages_rejects_orphan_tool_return() -> None:
    orphan = [
        {
            "kind": "request",
            "parts": [
                {
                    "part_kind": "tool-return",
                    "tool_name": "x",
                    "tool_call_id": "nope",
                    "content": "y",
                },
            ],
        },
    ]
    with pytest.raises(CatalogError, match="unknown tool_call_id"):
        from_pydantic_ai_messages(orphan)


def test_from_messages_rejects_unknown_kind() -> None:
    with pytest.raises(CatalogError, match="unknown kind"):
        from_pydantic_ai_messages([{"kind": "bogus", "parts": []}])


def test_from_messages_rejects_unknown_part_kind() -> None:
    with pytest.raises(CatalogError, match="unknown part_kind"):
        from_pydantic_ai_messages(
            [{"kind": "request", "parts": [{"part_kind": "bogus", "content": ""}]}]
        )


def test_from_messages_rejects_tool_call_without_id() -> None:
    with pytest.raises(CatalogError, match="missing 'tool_call_id'"):
        from_pydantic_ai_messages(
            [
                {
                    "kind": "response",
                    "parts": [
                        {"part_kind": "tool-call", "tool_name": "x", "args": {}},
                    ],
                }
            ]
        )


def test_round_trip_messages_lossless() -> None:
    items = from_pydantic_ai_messages(_TRANSCRIPT)
    rebuilt = to_pydantic_ai_messages(items)
    assert rebuilt == _TRANSCRIPT


def test_round_trip_ignores_items_without_msg_index() -> None:
    items = from_pydantic_ai_messages(_TRANSCRIPT)
    # Inject a non-provider item — should be dropped on encode.
    from contextweaver.types import ContextItem

    extra = ContextItem(id="other:1", kind=ItemKind.user_turn, text="ignored")
    rebuilt = to_pydantic_ai_messages([extra, *items])
    assert rebuilt == _TRANSCRIPT


def test_round_trip_retry_prompt_kind() -> None:
    transcript = [
        {
            "kind": "request",
            "parts": [{"part_kind": "retry-prompt", "content": "try again"}],
        }
    ]
    items = from_pydantic_ai_messages(transcript)
    assert items[0].metadata.get("retry") is True
    rebuilt = to_pydantic_ai_messages(items)
    assert rebuilt == transcript


def test_from_messages_requires_list() -> None:
    with pytest.raises(CatalogError, match="expects a list"):
        from_pydantic_ai_messages({"kind": "request"})  # type: ignore[arg-type]

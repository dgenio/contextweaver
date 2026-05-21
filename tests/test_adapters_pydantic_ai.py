"""Tests for contextweaver.adapters.pydantic_ai + .pydantic_ai_messages (issue #272)."""

from __future__ import annotations

import pytest

from contextweaver.adapters.pydantic_ai import (
    infer_pydantic_ai_namespace,
    load_pydantic_ai_catalog,
    pydantic_ai_tool_to_selectable,
    pydantic_ai_tools_to_catalog,
)
from contextweaver.adapters.pydantic_ai_messages import (
    from_pydantic_ai_messages,
    to_pydantic_ai_messages,
)
from contextweaver.exceptions import CatalogError
from contextweaver.types import ItemKind

# ---------------------------------------------------------------------------
# Namespace inference
# ---------------------------------------------------------------------------


def test_infer_pydantic_ai_namespace_underscore() -> None:
    assert infer_pydantic_ai_namespace("github_search") == "github"


def test_infer_pydantic_ai_namespace_dot() -> None:
    assert infer_pydantic_ai_namespace("calendar.create_event") == "calendar"


def test_infer_pydantic_ai_namespace_slash() -> None:
    assert infer_pydantic_ai_namespace("filesystem/read_file") == "filesystem"


def test_infer_pydantic_ai_namespace_empty() -> None:
    assert infer_pydantic_ai_namespace("") == "pydantic_ai"


def test_infer_pydantic_ai_namespace_single_segment() -> None:
    assert infer_pydantic_ai_namespace("search") == "pydantic_ai"


# ---------------------------------------------------------------------------
# Dict → SelectableItem conversion
# ---------------------------------------------------------------------------


def test_pydantic_ai_tool_to_selectable_minimal_dict() -> None:
    item = pydantic_ai_tool_to_selectable({"name": "search", "description": "Search the corpus."})
    assert item.kind == "tool"
    assert item.id == "pydantic_ai:search"
    assert item.name == "search"
    assert item.description == "Search the corpus."
    assert item.namespace == "pydantic_ai"
    assert item.tags == ["pydantic_ai"]
    assert item.args_schema == {}
    assert item.metadata == {}


def test_pydantic_ai_tool_to_selectable_inferred_namespace() -> None:
    item = pydantic_ai_tool_to_selectable(
        {"name": "github_search_repos", "description": "Search GitHub repos."}
    )
    assert item.namespace == "github"
    assert item.name == "search_repos"
    assert item.id == "pydantic_ai:github_search_repos"
    assert "pydantic_ai" in item.tags


def test_pydantic_ai_tool_to_selectable_explicit_namespace_overrides_inference() -> None:
    item = pydantic_ai_tool_to_selectable(
        {"name": "github_search_repos", "description": "x"},
        namespace="vcs",
    )
    assert item.namespace == "vcs"
    assert item.name == "github_search_repos"


def test_pydantic_ai_tool_to_selectable_preserves_user_tags() -> None:
    item = pydantic_ai_tool_to_selectable(
        {
            "name": "calendar.create_event",
            "description": "Schedule a meeting.",
            "tags": ["calendar", "write"],
        }
    )
    assert set(item.tags) == {"pydantic_ai", "calendar", "write"}


def test_pydantic_ai_tool_to_selectable_parameters_json_schema_preserved() -> None:
    schema = {"type": "object", "properties": {"q": {"type": "string"}}}
    item = pydantic_ai_tool_to_selectable(
        {
            "name": "search",
            "description": "Search.",
            "parameters_json_schema": schema,
        }
    )
    assert item.args_schema == schema


def test_pydantic_ai_tool_to_selectable_falls_back_to_args_schema() -> None:
    """``args_schema`` is accepted as a synonym for ``parameters_json_schema``."""
    schema = {"type": "object", "properties": {"q": {"type": "string"}}}
    item = pydantic_ai_tool_to_selectable(
        {"name": "search", "description": "Search.", "args_schema": schema}
    )
    assert item.args_schema == schema


def test_pydantic_ai_tool_to_selectable_strict_and_takes_ctx_pass_through() -> None:
    item = pydantic_ai_tool_to_selectable(
        {
            "name": "search",
            "description": "Search.",
            "strict": True,
            "takes_ctx": False,
        }
    )
    assert item.metadata == {"strict": True, "takes_ctx": False}


def test_pydantic_ai_tool_to_selectable_missing_name_raises() -> None:
    with pytest.raises(CatalogError, match="'name'"):
        pydantic_ai_tool_to_selectable({"description": "missing name"})


def test_pydantic_ai_tool_to_selectable_missing_description_raises() -> None:
    with pytest.raises(CatalogError, match="'description'"):
        pydantic_ai_tool_to_selectable({"name": "search"})


def test_pydantic_ai_tool_to_selectable_empty_name_raises() -> None:
    with pytest.raises(CatalogError, match="'name'"):
        pydantic_ai_tool_to_selectable({"name": "", "description": "x"})


# ---------------------------------------------------------------------------
# Batch conversion → Catalog
# ---------------------------------------------------------------------------


def test_pydantic_ai_tools_to_catalog_registers_every_item() -> None:
    catalog = pydantic_ai_tools_to_catalog(
        [
            {"name": "search", "description": "Search."},
            {"name": "browser.open", "description": "Open a page."},
        ]
    )
    ids = {item.id for item in catalog.all()}
    assert ids == {"pydantic_ai:search", "pydantic_ai:browser.open"}


def test_pydantic_ai_tools_to_catalog_uniform_namespace_override() -> None:
    catalog = pydantic_ai_tools_to_catalog(
        [
            {"name": "alpha", "description": "x"},
            {"name": "beta", "description": "y"},
        ],
        namespace="lab",
    )
    namespaces = {item.namespace for item in catalog.all()}
    assert namespaces == {"lab"}


# ---------------------------------------------------------------------------
# Live load_pydantic_ai_catalog
# ---------------------------------------------------------------------------


class _FakeTool:
    """Minimal duck-typed stand-in for ``pydantic_ai.tools.Tool``."""

    def __init__(
        self,
        name: str,
        description: str,
        schema: dict[str, object] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.parameters_json_schema = schema
        self.tags: list[str] = []
        self.strict = True
        self.takes_ctx = False


class _FakeToolset:
    def __init__(self, tools: list[_FakeTool]) -> None:
        self.tools = tools


def test_load_pydantic_ai_catalog_accepts_duck_typed_tools() -> None:
    catalog = load_pydantic_ai_catalog(
        [
            _FakeTool("search", "Search."),
            _FakeTool("github_create_issue", "Open an issue."),
        ]
    )
    ids = {item.id for item in catalog.all()}
    assert ids == {"pydantic_ai:search", "pydantic_ai:github_create_issue"}


def test_load_pydantic_ai_catalog_flattens_a_toolset() -> None:
    toolset = _FakeToolset([_FakeTool("alpha", "x"), _FakeTool("beta", "y")])
    catalog = load_pydantic_ai_catalog([toolset])
    ids = {item.id for item in catalog.all()}
    assert ids == {"pydantic_ai:alpha", "pydantic_ai:beta"}


def test_load_pydantic_ai_catalog_rejects_object_without_name() -> None:
    class Bogus:
        description = "no name"

    with pytest.raises(CatalogError, match="'name'"):
        load_pydantic_ai_catalog([Bogus()])


# ---------------------------------------------------------------------------
# Message round-trip
# ---------------------------------------------------------------------------

_MESSAGES: list[dict[str, object]] = [
    {
        "kind": "request",
        "parts": [
            {"part_kind": "system-prompt", "content": "You are helpful."},
            {"part_kind": "user-prompt", "content": "find typescript repos"},
        ],
    },
    {
        "kind": "response",
        "parts": [
            {"part_kind": "text", "content": "I'll search GitHub first."},
            {
                "part_kind": "tool-call",
                "tool_call_id": "call_001",
                "tool_name": "github_search_repos",
                "args": {"q": "typescript"},
            },
        ],
    },
    {
        "kind": "request",
        "parts": [
            {
                "part_kind": "tool-return",
                "tool_call_id": "call_001",
                "tool_name": "github_search_repos",
                "content": "5 results",
            }
        ],
    },
]


def test_from_pydantic_ai_messages_decodes_each_part_to_an_item() -> None:
    items = from_pydantic_ai_messages(_MESSAGES)
    # 2 + 2 + 1 parts
    assert len(items) == 5
    kinds = [item.kind for item in items]
    assert kinds == [
        ItemKind.policy,
        ItemKind.user_turn,
        ItemKind.agent_msg,
        ItemKind.tool_call,
        ItemKind.tool_result,
    ]
    tool_result = items[4]
    # tool_result links back to the originating tool_call via parent_id
    assert tool_result.parent_id == "pydantic_ai:tool_call:call_001"
    assert tool_result.metadata["tool_call_id"] == "call_001"


def test_from_pydantic_ai_messages_round_trip_is_exact() -> None:
    """``args`` are normalised to a canonical JSON string on the way through.

    Pydantic AI's ``ToolCallPart.args`` accepts either ``str`` or ``dict`` at
    construction time, but ``ModelMessage.model_dump_json()`` always emits
    the string form.  The decoder pins that canonical form so the round-trip
    is byte-stable regardless of which input shape the caller supplied.
    """
    items = from_pydantic_ai_messages(_MESSAGES)
    re_encoded = to_pydantic_ai_messages(items)
    # Pre-compute what the canonical-args view of the input looks like.
    expected = [
        {
            "kind": "request",
            "parts": [
                {"part_kind": "system-prompt", "content": "You are helpful."},
                {"part_kind": "user-prompt", "content": "find typescript repos"},
            ],
        },
        {
            "kind": "response",
            "parts": [
                {"part_kind": "text", "content": "I'll search GitHub first."},
                {
                    "part_kind": "tool-call",
                    "tool_call_id": "call_001",
                    "tool_name": "github_search_repos",
                    "args": '{"q": "typescript"}',
                },
            ],
        },
        {
            "kind": "request",
            "parts": [
                {
                    "part_kind": "tool-return",
                    "tool_call_id": "call_001",
                    "tool_name": "github_search_repos",
                    "content": "5 results",
                }
            ],
        },
    ]
    assert re_encoded == expected


def test_from_pydantic_ai_messages_round_trip_preserves_string_args() -> None:
    """When the input already supplies stringified ``args``, no shape change occurs."""
    string_args_messages = [
        {
            "kind": "response",
            "parts": [
                {
                    "part_kind": "tool-call",
                    "tool_call_id": "call_001",
                    "tool_name": "noop",
                    "args": '{"q": "typescript"}',
                }
            ],
        }
    ]
    items = from_pydantic_ai_messages(string_args_messages)
    re_encoded = to_pydantic_ai_messages(items)
    assert re_encoded == string_args_messages


def test_from_pydantic_ai_messages_rejects_non_list() -> None:
    with pytest.raises(CatalogError, match="from_pydantic_ai_messages expects a list"):
        from_pydantic_ai_messages("not a list")  # type: ignore[arg-type]


def test_from_pydantic_ai_messages_rejects_unknown_part_kind() -> None:
    with pytest.raises(CatalogError, match="unknown part_kind"):
        from_pydantic_ai_messages(
            [{"kind": "request", "parts": [{"part_kind": "weird-kind", "content": "x"}]}]
        )


def test_from_pydantic_ai_messages_rejects_orphan_tool_return() -> None:
    with pytest.raises(CatalogError, match="unknown tool_call_id"):
        from_pydantic_ai_messages(
            [
                {
                    "kind": "request",
                    "parts": [
                        {
                            "part_kind": "tool-return",
                            "tool_call_id": "missing",
                            "tool_name": "x",
                            "content": "y",
                        }
                    ],
                }
            ]
        )


def test_from_pydantic_ai_messages_accepts_model_dump_able_objects() -> None:
    class FakeModelMessage:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def model_dump(self) -> dict[str, object]:
            return self._payload

    items = from_pydantic_ai_messages([FakeModelMessage(m) for m in _MESSAGES])
    assert len(items) == 5


def test_to_pydantic_ai_messages_requires_msg_index_metadata() -> None:
    items = from_pydantic_ai_messages(_MESSAGES)
    items[0].metadata.pop("msg_index")
    with pytest.raises(CatalogError, match="missing 'msg_index'"):
        to_pydantic_ai_messages(items)

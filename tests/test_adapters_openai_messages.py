"""Tests for the OpenAI Chat Completions message-array adapter (issue #219)."""

from __future__ import annotations

import sys

import pytest

from contextweaver.adapters.openai_messages import (
    from_openai_messages,
    to_openai_messages,
)
from contextweaver.context.manager import ContextManager
from contextweaver.exceptions import CatalogError
from contextweaver.types import ItemKind, Phase

# ---------------------------------------------------------------------------
# Fixtures (representative OpenAI message arrays)
# ---------------------------------------------------------------------------


SIMPLE_CHAT: list[dict] = [
    {"role": "system", "content": "You are a concise assistant."},
    {"role": "user", "content": "What is the capital of France?"},
    {"role": "assistant", "content": "Paris."},
]


CHAT_WITH_TOOL_CALL: list[dict] = [
    {"role": "user", "content": "What's the weather in Paris?"},
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_abc123",
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "arguments": '{"city": "Paris"}',
                },
            }
        ],
    },
    {
        "role": "tool",
        "tool_call_id": "call_abc123",
        "content": '{"temperature_c": 18, "condition": "cloudy"}',
    },
    {"role": "assistant", "content": "It's 18 °C and cloudy in Paris."},
]


MULTI_TOOL_CALL: list[dict] = [
    {"role": "user", "content": "Compare weather in Paris and London."},
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_p",
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "arguments": '{"city": "Paris"}',
                },
            },
            {
                "id": "call_l",
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "arguments": '{"city": "London"}',
                },
            },
        ],
    },
    {
        "role": "tool",
        "tool_call_id": "call_p",
        "content": '{"temperature_c": 18}',
    },
    {
        "role": "tool",
        "tool_call_id": "call_l",
        "content": '{"temperature_c": 14}',
    },
    {"role": "assistant", "content": "Paris is warmer (18 °C vs 14 °C)."},
]


# ---------------------------------------------------------------------------
# from_openai_messages: role mapping
# ---------------------------------------------------------------------------


def test_from_openai_messages_simple_chat_role_mapping() -> None:
    items = from_openai_messages(SIMPLE_CHAT)
    assert [item.kind for item in items] == [
        ItemKind.policy,
        ItemKind.user_turn,
        ItemKind.agent_msg,
    ]
    assert items[0].text == "You are a concise assistant."
    assert items[1].text == "What is the capital of France?"
    assert items[2].text == "Paris."


def test_from_openai_messages_assistant_tool_call_splits() -> None:
    """Assistant message with tool_calls expands to agent_msg + tool_call items."""
    items = from_openai_messages(CHAT_WITH_TOOL_CALL)
    # user, assistant (agent_msg), tool_call, tool_result, assistant
    kinds = [item.kind for item in items]
    assert kinds == [
        ItemKind.user_turn,
        ItemKind.agent_msg,
        ItemKind.tool_call,
        ItemKind.tool_result,
        ItemKind.agent_msg,
    ]


def test_from_openai_messages_tool_result_parent_id_chain() -> None:
    """tool_result.parent_id must reference the originating tool_call item."""
    items = from_openai_messages(CHAT_WITH_TOOL_CALL)
    tool_call_item = next(it for it in items if it.kind is ItemKind.tool_call)
    tool_result_item = next(it for it in items if it.kind is ItemKind.tool_result)
    assert tool_result_item.parent_id == tool_call_item.id
    assert tool_result_item.metadata["tool_call_id"] == "call_abc123"


def test_from_openai_messages_tool_call_id_roundtrips_to_item_id() -> None:
    items = from_openai_messages(CHAT_WITH_TOOL_CALL)
    tool_call_item = next(it for it in items if it.kind is ItemKind.tool_call)
    # tool_call_id is recoverable from the ContextItem.id (the round-trip
    # invariant required by issue #219).
    assert tool_call_item.id.endswith("call_abc123")
    assert tool_call_item.metadata["function_name"] == "get_weather"


# ---------------------------------------------------------------------------
# Round-trip equality (the issue's hard requirement)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture",
    [SIMPLE_CHAT, CHAT_WITH_TOOL_CALL, MULTI_TOOL_CALL],
    ids=["simple_chat", "single_tool_call", "multi_tool_call"],
)
def test_roundtrip_preserves_structure(fixture: list[dict]) -> None:
    """to_openai_messages(from_openai_messages(x)) == x (structural)."""
    items = from_openai_messages(fixture)
    rebuilt = to_openai_messages(items)
    assert rebuilt == fixture


# ---------------------------------------------------------------------------
# into=ContextManager: 5-line drop-in path
# ---------------------------------------------------------------------------


def test_from_openai_messages_into_manager_appends_to_event_log() -> None:
    mgr = ContextManager()
    items = from_openai_messages(CHAT_WITH_TOOL_CALL, into=mgr)
    # Every returned item must also be in the manager's event log in order.
    logged = list(mgr.event_log.all())
    assert [it.id for it in logged] == [it.id for it in items]


def test_from_openai_messages_5_line_drop_in_builds_pack() -> None:
    """Issue #219 success metric: ≤5-line drop-in produces a working pack."""
    mgr = ContextManager()
    from_openai_messages(CHAT_WITH_TOOL_CALL, into=mgr)
    pack = mgr.build_sync(phase=Phase.answer, query="What was the weather?")
    assert pack.prompt
    assert pack.stats.included_count > 0


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_from_openai_messages_rejects_non_list() -> None:
    with pytest.raises(CatalogError, match="expects a list"):
        from_openai_messages("not a list")  # type: ignore[arg-type]


def test_from_openai_messages_rejects_unknown_role() -> None:
    with pytest.raises(CatalogError, match="unknown role"):
        from_openai_messages([{"role": "spaceship", "content": "x"}])


def test_from_openai_messages_rejects_tool_without_call_id() -> None:
    with pytest.raises(CatalogError, match="missing tool_call_id"):
        from_openai_messages([{"role": "tool", "content": "x"}])


def test_from_openai_messages_rejects_tool_call_without_id() -> None:
    msg = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"type": "function", "function": {"name": "x"}}],
    }
    with pytest.raises(CatalogError, match="missing id"):
        from_openai_messages([msg])


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_from_openai_messages_handles_assistant_content_null() -> None:
    """content=None on assistant tool-call message round-trips back to None."""
    msg = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_x",
                "type": "function",
                "function": {"name": "f", "arguments": "{}"},
            }
        ],
    }
    items = from_openai_messages([msg])
    rebuilt = to_openai_messages(items)
    assert rebuilt[0]["content"] is None


def test_from_openai_messages_handles_multimodal_user_content_list() -> None:
    """User messages with content as a list of parts collapse to joined text."""
    msg = {
        "role": "user",
        "content": [
            {"type": "text", "text": "Describe this:"},
            {"type": "image_url", "image_url": {"url": "https://example.com/x.jpg"}},
        ],
    }
    items = from_openai_messages([msg])
    assert "Describe this:" in items[0].text


def test_module_does_not_import_provider_sdk_at_load_time() -> None:
    """Issue #219 design constraint: no provider SDK import at module level."""
    # The openai_messages module is already imported by these tests; assert
    # that neither openai nor any openai_* package leaked into sys.modules
    # as a transitive import of the adapter.
    assert "openai" not in sys.modules
    assert "anthropic" not in sys.modules
    assert "google.generativeai" not in sys.modules

"""Tests for the Anthropic Messages API adapter (issue #222)."""

from __future__ import annotations

import sys

import pytest

from contextweaver.adapters.anthropic_messages import (
    from_anthropic_messages,
    to_anthropic_messages,
)
from contextweaver.context.manager import ContextManager
from contextweaver.exceptions import CatalogError
from contextweaver.types import ItemKind, Phase

# ---------------------------------------------------------------------------
# Fixtures (representative Anthropic Messages API arrays)
# ---------------------------------------------------------------------------


SIMPLE_CHAT: list[dict] = [
    {
        "role": "user",
        "content": [{"type": "text", "text": "Hello, who are you?"}],
    },
    {
        "role": "assistant",
        "content": [{"type": "text", "text": "I'm Claude, an AI assistant."}],
    },
]


CHAT_WITH_TOOL_USE: list[dict] = [
    {
        "role": "user",
        "content": [{"type": "text", "text": "What's the weather in Tokyo?"}],
    },
    {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "I'll check the weather."},
            {
                "type": "tool_use",
                "id": "toolu_01ABC",
                "name": "get_weather",
                "input": {"city": "Tokyo"},
            },
        ],
    },
    {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_01ABC",
                "content": '{"temperature_c": 22}',
            }
        ],
    },
    {
        "role": "assistant",
        "content": [{"type": "text", "text": "Tokyo is 22 °C."}],
    },
]


STRING_SHORTHAND: list[dict] = [
    # Anthropic accepts `content: "..."` as a shorthand for a single text block.
    {"role": "user", "content": "Hi there!"},
    {"role": "assistant", "content": "Hello! How can I help?"},
]


# ---------------------------------------------------------------------------
# from_anthropic_messages: role + block mapping
# ---------------------------------------------------------------------------


def test_from_anthropic_messages_simple_chat() -> None:
    items = from_anthropic_messages(SIMPLE_CHAT)
    assert [item.kind for item in items] == [ItemKind.user_turn, ItemKind.agent_msg]
    assert items[0].text == "Hello, who are you?"
    assert items[1].text == "I'm Claude, an AI assistant."


def test_from_anthropic_messages_tool_use_splits_into_text_and_tool_call() -> None:
    items = from_anthropic_messages(CHAT_WITH_TOOL_USE)
    kinds = [item.kind for item in items]
    # user_turn, agent_msg (text), tool_call, tool_result, agent_msg
    assert kinds == [
        ItemKind.user_turn,
        ItemKind.agent_msg,
        ItemKind.tool_call,
        ItemKind.tool_result,
        ItemKind.agent_msg,
    ]


def test_from_anthropic_messages_tool_use_id_chain() -> None:
    items = from_anthropic_messages(CHAT_WITH_TOOL_USE)
    tool_call = next(it for it in items if it.kind is ItemKind.tool_call)
    tool_result = next(it for it in items if it.kind is ItemKind.tool_result)
    assert tool_call.metadata["tool_use_id"] == "toolu_01ABC"
    assert tool_result.metadata["tool_use_id"] == "toolu_01ABC"
    # parent_id chain back to the originating tool_use item.
    assert tool_result.parent_id == tool_call.id


def test_from_anthropic_messages_preserves_content_block_ordering() -> None:
    """Anthropic models care about block order — round-trip must preserve it."""
    items = from_anthropic_messages(CHAT_WITH_TOOL_USE)
    # The assistant turn at msg_index=1 has 2 blocks: text then tool_use.
    assistant_blocks = [it for it in items if it.metadata.get("msg_index") == 1]
    assert [it.metadata["block_index"] for it in assistant_blocks] == [0, 1]
    assert [it.metadata["block_type"] for it in assistant_blocks] == [
        "text",
        "tool_use",
    ]


# ---------------------------------------------------------------------------
# Round-trip equality
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture",
    [SIMPLE_CHAT, CHAT_WITH_TOOL_USE, STRING_SHORTHAND],
    ids=["simple_chat", "tool_use_chain", "string_shorthand"],
)
def test_roundtrip_preserves_structure(fixture: list[dict]) -> None:
    items = from_anthropic_messages(fixture)
    rebuilt = to_anthropic_messages(items)
    assert rebuilt == fixture


# ---------------------------------------------------------------------------
# Drop-in path via into=
# ---------------------------------------------------------------------------


def test_from_anthropic_messages_into_manager_builds_pack() -> None:
    mgr = ContextManager()
    from_anthropic_messages(CHAT_WITH_TOOL_USE, into=mgr)
    pack = mgr.build_sync(phase=Phase.answer, query="What was the temperature?")
    assert pack.prompt
    assert pack.stats.included_count > 0


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_from_anthropic_messages_rejects_non_list() -> None:
    with pytest.raises(CatalogError, match="expects a list"):
        from_anthropic_messages("nope")  # type: ignore[arg-type]


def test_from_anthropic_messages_rejects_unknown_role() -> None:
    with pytest.raises(CatalogError, match="unknown role"):
        from_anthropic_messages([{"role": "robot", "content": "x"}])


def test_from_anthropic_messages_rejects_tool_use_without_id() -> None:
    msgs = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": "f", "input": {}}],
        }
    ]
    with pytest.raises(CatalogError, match="missing 'id'"):
        from_anthropic_messages(msgs)


def test_from_anthropic_messages_rejects_tool_result_without_tool_use_id() -> None:
    msgs = [{"role": "user", "content": [{"type": "tool_result", "content": "x"}]}]
    with pytest.raises(CatalogError, match="missing 'tool_use_id'"):
        from_anthropic_messages(msgs)


def test_from_anthropic_messages_rejects_unknown_block_type() -> None:
    msgs = [{"role": "user", "content": [{"type": "alien_block"}]}]
    with pytest.raises(CatalogError, match="unsupported"):
        from_anthropic_messages(msgs)


def test_to_anthropic_messages_rejects_items_without_msg_index() -> None:
    from contextweaver.types import ContextItem

    items = [ContextItem(id="x", kind=ItemKind.user_turn, text="hi", metadata={"role": "user"})]
    with pytest.raises(CatalogError, match="msg_index"):
        to_anthropic_messages(items)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_tool_result_with_is_error_preserved() -> None:
    """is_error flag survives the round-trip."""
    msgs = [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_X",
                    "name": "fail",
                    "input": {},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_X",
                    "content": "something broke",
                    "is_error": True,
                }
            ],
        },
    ]
    items = from_anthropic_messages(msgs)
    tool_result = next(it for it in items if it.kind is ItemKind.tool_result)
    assert tool_result.metadata["is_error"] is True
    assert to_anthropic_messages(items) == msgs


def test_from_anthropic_messages_rejects_orphan_tool_result() -> None:
    """tool_result with tool_use_id not announced by a prior tool_use → CatalogError.

    PR #230 review: mirrors the openai_messages orphan check.
    """
    msgs = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_orphan",
                    "content": "result without a call",
                }
            ],
        }
    ]
    with pytest.raises(CatalogError, match="does not match any prior assistant tool_use"):
        from_anthropic_messages(msgs)


def test_roundtrip_preserves_explicit_is_error_false() -> None:
    """Explicit `is_error: False` survives the round-trip (PR #230 review)."""
    msgs = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "toolu_e", "name": "f", "input": {}}],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_e",
                    "content": "ok",
                    "is_error": False,
                }
            ],
        },
    ]
    rebuilt = to_anthropic_messages(from_anthropic_messages(msgs))
    assert rebuilt == msgs
    # Defensive: the False must appear explicitly, not be stripped.
    assert rebuilt[1]["content"][0]["is_error"] is False


def test_roundtrip_preserves_unknown_block_fields_like_cache_control() -> None:
    """Unknown provider fields on text blocks survive the round-trip.

    PR #230 review: `cache_control` (and any other future Anthropic block
    attribute we don't explicitly decode) must not be dropped.
    """
    msgs = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Hello there.",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
    ]
    rebuilt = to_anthropic_messages(from_anthropic_messages(msgs))
    assert rebuilt == msgs
    assert rebuilt[0]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_module_does_not_import_provider_sdk_at_load_time() -> None:
    """No provider SDK leaked into sys.modules through the adapter import.

    Runs in a fresh subprocess so the invariant is independent of whatever
    other tests in the session may have pulled into ``sys.modules``
    transitively.
    """
    import subprocess

    script = (
        "import sys\n"
        "import contextweaver.adapters.anthropic_messages  # noqa: F401\n"
        "assert 'anthropic' not in sys.modules\n"
        "assert 'openai' not in sys.modules\n"
        "assert 'google.generativeai' not in sys.modules\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"anthropic_messages leaked a provider SDK at import time: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )

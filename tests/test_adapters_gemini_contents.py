"""Tests for the Google Gemini ``contents`` adapter (issue #222)."""

from __future__ import annotations

import sys

import pytest

from contextweaver.adapters.gemini_contents import (
    from_gemini_contents,
    to_gemini_contents,
)
from contextweaver.context.manager import ContextManager
from contextweaver.exceptions import CatalogError
from contextweaver.types import ItemKind, Phase

# ---------------------------------------------------------------------------
# Fixtures (representative Gemini contents arrays)
# ---------------------------------------------------------------------------


SIMPLE_CHAT: list[dict] = [
    {"role": "user", "parts": [{"text": "What's 2 + 2?"}]},
    {"role": "model", "parts": [{"text": "4."}]},
]


CHAT_WITH_FUNCTION_CALL: list[dict] = [
    {"role": "user", "parts": [{"text": "What's the time in Berlin?"}]},
    {
        "role": "model",
        "parts": [
            {"text": "Let me check the time."},
            {
                "functionCall": {
                    "name": "get_time",
                    "args": {"city": "Berlin"},
                }
            },
        ],
    },
    {
        "role": "function",
        "parts": [
            {
                "functionResponse": {
                    "name": "get_time",
                    "response": {"time": "15:30"},
                }
            }
        ],
    },
    {"role": "model", "parts": [{"text": "It's 15:30 in Berlin."}]},
]


# ---------------------------------------------------------------------------
# from_gemini_contents: role + part mapping
# ---------------------------------------------------------------------------


def test_from_gemini_contents_simple_chat() -> None:
    items = from_gemini_contents(SIMPLE_CHAT)
    assert [item.kind for item in items] == [ItemKind.user_turn, ItemKind.agent_msg]
    assert items[0].text == "What's 2 + 2?"
    assert items[1].text == "4."


def test_from_gemini_contents_function_call_chain() -> None:
    items = from_gemini_contents(CHAT_WITH_FUNCTION_CALL)
    kinds = [item.kind for item in items]
    assert kinds == [
        ItemKind.user_turn,
        ItemKind.agent_msg,
        ItemKind.tool_call,
        ItemKind.tool_result,
        ItemKind.agent_msg,
    ]


def test_from_gemini_contents_synthesised_id_is_deterministic() -> None:
    """Issue #222 design constraint: synthesise ID from name + position."""
    items1 = from_gemini_contents(CHAT_WITH_FUNCTION_CALL)
    items2 = from_gemini_contents(CHAT_WITH_FUNCTION_CALL)
    ids1 = [it.id for it in items1]
    ids2 = [it.id for it in items2]
    assert ids1 == ids2
    # The synthesised ID format is "<name>:<msg_idx>:<part_idx>" for the call,
    # and the response is linked back via parent_id.
    tool_call = next(it for it in items1 if it.kind is ItemKind.tool_call)
    assert "get_time:1:1" in tool_call.id


def test_from_gemini_contents_parent_id_chain() -> None:
    items = from_gemini_contents(CHAT_WITH_FUNCTION_CALL)
    tool_call = next(it for it in items if it.kind is ItemKind.tool_call)
    tool_result = next(it for it in items if it.kind is ItemKind.tool_result)
    assert tool_result.parent_id == tool_call.id


def test_from_gemini_contents_preserves_part_ordering() -> None:
    """Multi-part messages must preserve original part_index order."""
    items = from_gemini_contents(CHAT_WITH_FUNCTION_CALL)
    model_turn_parts = [it for it in items if it.metadata.get("msg_index") == 1]
    assert [it.metadata["part_index"] for it in model_turn_parts] == [0, 1]


# ---------------------------------------------------------------------------
# Round-trip equality
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture",
    [SIMPLE_CHAT, CHAT_WITH_FUNCTION_CALL],
    ids=["simple_chat", "function_call_chain"],
)
def test_roundtrip_preserves_structure(fixture: list[dict]) -> None:
    items = from_gemini_contents(fixture)
    rebuilt = to_gemini_contents(items)
    assert rebuilt == fixture


# ---------------------------------------------------------------------------
# Drop-in path via into=
# ---------------------------------------------------------------------------


def test_from_gemini_contents_into_manager_builds_pack() -> None:
    mgr = ContextManager()
    from_gemini_contents(CHAT_WITH_FUNCTION_CALL, into=mgr)
    pack = mgr.build_sync(phase=Phase.answer, query="What time was it?")
    assert pack.prompt
    assert pack.stats.included_count > 0


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_from_gemini_contents_rejects_non_list() -> None:
    with pytest.raises(CatalogError, match="expects a list"):
        from_gemini_contents("nope")  # type: ignore[arg-type]


def test_from_gemini_contents_rejects_unknown_role() -> None:
    with pytest.raises(CatalogError, match="unknown role"):
        from_gemini_contents([{"role": "system", "parts": [{"text": "x"}]}])


def test_from_gemini_contents_rejects_missing_parts_list() -> None:
    with pytest.raises(CatalogError, match="non-list parts"):
        from_gemini_contents([{"role": "user", "parts": "nope"}])


def test_from_gemini_contents_rejects_response_without_matching_call() -> None:
    msgs = [
        {
            "role": "function",
            "parts": [
                {
                    "functionResponse": {
                        "name": "orphan_function",
                        "response": {"x": 1},
                    }
                }
            ],
        }
    ]
    with pytest.raises(CatalogError, match="no matching prior functionCall"):
        from_gemini_contents(msgs)


def test_from_gemini_contents_rejects_unknown_part_type() -> None:
    msgs = [{"role": "user", "parts": [{"alien": "x"}]}]
    with pytest.raises(CatalogError, match="no recognised content"):
        from_gemini_contents(msgs)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_two_function_calls_with_same_name_get_distinct_ids() -> None:
    """FIFO matching: two calls with the same name pair with two responses."""
    msgs = [
        {
            "role": "model",
            "parts": [
                {"functionCall": {"name": "fetch", "args": {"q": "a"}}},
                {"functionCall": {"name": "fetch", "args": {"q": "b"}}},
            ],
        },
        {
            "role": "function",
            "parts": [
                {"functionResponse": {"name": "fetch", "response": {"r": "a"}}},
                {"functionResponse": {"name": "fetch", "response": {"r": "b"}}},
            ],
        },
    ]
    items = from_gemini_contents(msgs)
    tool_calls = [it for it in items if it.kind is ItemKind.tool_call]
    tool_results = [it for it in items if it.kind is ItemKind.tool_result]
    assert len({it.id for it in tool_calls}) == 2  # distinct IDs
    # Responses pair with calls FIFO by name.
    assert tool_results[0].parent_id == tool_calls[0].id
    assert tool_results[1].parent_id == tool_calls[1].id
    # And the round-trip preserves the shape.
    assert to_gemini_contents(items) == msgs


def test_to_gemini_contents_rejects_empty_text_content() -> None:
    """A turn that renders to a blank text part must not emit empty content.

    Gemini rejects contents whose parts carry no renderable text; the adapter
    fails fast at conversion time instead of letting the API 400 escape.
    """
    from contextweaver.types import ContextItem

    items = [
        ContextItem(
            id="g",
            kind=ItemKind.agent_msg,
            text="",
            metadata={
                "role": "model",
                "msg_index": 0,
                "part_index": 0,
                "part_type": "text",
            },
        )
    ]
    with pytest.raises(CatalogError, match="non-empty content"):
        to_gemini_contents(items)


def test_to_gemini_contents_allows_function_call_only_content() -> None:
    """A content carrying a functionCall part is non-empty even without text."""
    msgs = [
        {
            "role": "model",
            "parts": [{"functionCall": {"name": "fetch", "args": {"q": "a"}}}],
        }
    ]
    assert to_gemini_contents(from_gemini_contents(msgs)) == msgs


def test_to_gemini_contents_rejects_whitespace_only_text_content() -> None:
    """Whitespace-only text is treated as empty, mirroring the Anthropic encoder."""
    from contextweaver.types import ContextItem

    items = [
        ContextItem(
            id="g",
            kind=ItemKind.agent_msg,
            text="   \n\t ",
            metadata={
                "role": "model",
                "msg_index": 0,
                "part_index": 0,
                "part_type": "text",
            },
        )
    ]
    with pytest.raises(CatalogError, match="non-empty content"):
        to_gemini_contents(items)


def test_to_gemini_contents_allows_blank_text_with_function_call() -> None:
    """A blank text part does not empty a content that also carries a functionCall."""
    msgs = [
        {
            "role": "model",
            "parts": [
                {"text": ""},
                {"functionCall": {"name": "fetch", "args": {"q": "a"}}},
            ],
        }
    ]
    assert to_gemini_contents(from_gemini_contents(msgs)) == msgs


def test_module_does_not_import_provider_sdk_at_load_time() -> None:
    """Issue #222 design constraint: no provider SDK import at module level.

    Runs in a fresh subprocess so the invariant is independent of whatever
    other tests in the session may have pulled into ``sys.modules``
    transitively.
    """
    import subprocess

    script = (
        "import sys\n"
        "import contextweaver.adapters.gemini_contents  # noqa: F401\n"
        "assert 'google.generativeai' not in sys.modules\n"
        "assert 'openai' not in sys.modules\n"
        "assert 'anthropic' not in sys.modules\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"gemini_contents leaked a provider SDK at import time: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )

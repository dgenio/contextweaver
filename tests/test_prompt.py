"""Tests for contextweaver.context.prompt."""

from __future__ import annotations

from contextweaver.context.prompt import render_context, render_item
from contextweaver.types import ArtifactRef, ContextItem, ItemKind


def test_render_item_user_turn() -> None:
    item = ContextItem(id="i1", kind=ItemKind.user_turn, text="Hello!")
    rendered = render_item(item)
    assert "[USER]" in rendered
    assert "Hello!" in rendered


def test_render_item_tool_result() -> None:
    item = ContextItem(id="i1", kind=ItemKind.tool_result, text="42 rows")
    rendered = render_item(item)
    assert "TOOL RESULT" in rendered


def test_render_item_with_artifact_ref() -> None:
    ref = ArtifactRef(handle="h1", media_type="text/plain", size_bytes=100)
    item = ContextItem(id="i1", kind=ItemKind.tool_result, text="summary", artifact_ref=ref)
    rendered = render_item(item)
    assert "artifact:h1" in rendered


def test_render_context_empty() -> None:
    assert render_context([]) == ""


def test_render_context_single_item() -> None:
    item = ContextItem(id="i1", kind=ItemKind.user_turn, text="Hi")
    result = render_context([item])
    assert "Hi" in result


def test_render_context_multiple_items() -> None:
    items = [
        ContextItem(id="i1", kind=ItemKind.user_turn, text="Question"),
        ContextItem(id="i2", kind=ItemKind.agent_msg, text="Answer"),
    ]
    result = render_context(items)
    assert "Question" in result
    assert "Answer" in result


def test_render_context_header_footer() -> None:
    item = ContextItem(id="i1", kind=ItemKind.user_turn, text="body")
    result = render_context([item], header="HEADER", footer="FOOTER")
    assert result.startswith("HEADER")
    assert result.endswith("FOOTER")

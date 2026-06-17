"""Tests for contextweaver.context.prompt."""

from __future__ import annotations

from contextweaver.context.prompt import passthrough_renderer, render_context, render_item
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


def test_render_item_artifact_handle_not_double_prefixed() -> None:
    # #313: firewall stores handles already namespaced as ``artifact:<id>``;
    # the renderer must not produce ``[artifact:artifact:...]``.
    ref = ArtifactRef(handle="artifact:tr1", media_type="text/plain", size_bytes=100)
    item = ContextItem(id="tr1", kind=ItemKind.tool_result, text="summary", artifact_ref=ref)
    rendered = render_item(item)
    assert "[artifact:tr1]" in rendered
    assert "artifact:artifact:" not in rendered


def test_render_item_artifact_handle_prefixed_when_bare() -> None:
    # A bare handle (no ``artifact:`` namespace) is still framed as an artifact.
    ref = ArtifactRef(handle="h1", media_type="text/plain", size_bytes=100)
    item = ContextItem(id="i1", kind=ItemKind.tool_result, text="summary", artifact_ref=ref)
    assert "[artifact:h1]" in render_item(item)


def test_render_item_tool_call_includes_function_name() -> None:
    # #308: adapters keep the tool name in metadata, not text; surface it.
    item = ContextItem(
        id="c1",
        kind=ItemKind.tool_call,
        text='{"city":"NYC"}',
        metadata={"function_name": "get_weather"},
    )
    assert render_item(item) == '[TOOL CALL]\nget_weather({"city":"NYC"})'


def test_render_item_tool_result_includes_function_name() -> None:
    item = ContextItem(
        id="r1",
        kind=ItemKind.tool_result,
        text="Sunny, 72F",
        metadata={"function_name": "get_weather"},
    )
    assert render_item(item) == "[TOOL RESULT]\nget_weather: Sunny, 72F"


def test_render_item_tool_call_without_function_name_unchanged() -> None:
    # No metadata -> body is the raw text (back-compatible default).
    item = ContextItem(id="c1", kind=ItemKind.tool_call, text='{"city":"NYC"}')
    assert render_item(item) == '[TOOL CALL]\n{"city":"NYC"}'


def test_render_item_tool_call_empty_args_renders_bare_call() -> None:
    # #308 edge case: empty args -> ``name()`` with no argument body.
    item = ContextItem(
        id="c1",
        kind=ItemKind.tool_call,
        text="",
        metadata={"function_name": "now"},
    )
    assert render_item(item) == "[TOOL CALL]\nnow()"


def test_render_item_empty_function_name_falls_back_to_text() -> None:
    # #308 edge case: an empty function_name is ignored (treated as absent).
    item = ContextItem(
        id="c1",
        kind=ItemKind.tool_call,
        text='{"city":"NYC"}',
        metadata={"function_name": ""},
    )
    assert render_item(item) == '[TOOL CALL]\n{"city":"NYC"}'


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


def test_render_item_retrieved_doc_label() -> None:
    """retrieved_doc renders under its own section label (#411)."""
    item = ContextItem(id="r1", kind=ItemKind.retrieved_doc, text="evidence")
    assert render_item(item).startswith("[RETRIEVED]")


def test_render_item_section_override() -> None:
    """metadata['section'] overrides presentation without touching the kind (#411)."""
    item = ContextItem(
        id="i1",
        kind=ItemKind.doc_snippet,
        text="cluster body",
        metadata={"section": "CLUSTER"},
    )
    rendered = render_item(item)
    assert rendered.startswith("[CLUSTER]")
    # The filtering kind is unchanged — only presentation moved.
    assert item.kind is ItemKind.doc_snippet


def test_render_item_blank_section_override_falls_back() -> None:
    """An empty or whitespace-only section override falls back to the kind label (#411)."""
    for blank in ("", "   "):
        item = ContextItem(id="i1", kind=ItemKind.user_turn, text="hi", metadata={"section": blank})
        assert render_item(item).startswith("[USER]")


def test_render_item_section_override_is_stripped() -> None:
    """A padded section override is trimmed so the header is clean (#411)."""
    item = ContextItem(
        id="i1", kind=ItemKind.doc_snippet, text="body", metadata={"section": "  CLUSTER  "}
    )
    assert render_item(item).startswith("[CLUSTER]")


def test_passthrough_renderer_joins_raw_text() -> None:
    """passthrough_renderer imposes no section layout (#410)."""
    items = [
        ContextItem(id="i1", kind=ItemKind.user_turn, text="first"),
        ContextItem(id="i2", kind=ItemKind.agent_msg, text="second"),
    ]
    result = passthrough_renderer(items)
    assert result == "first\n\nsecond"
    assert "[USER]" not in result

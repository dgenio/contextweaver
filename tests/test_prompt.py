"""Tests for contextweaver.context.prompt -- render_context per phase, PromptBuilder."""

from __future__ import annotations

from contextweaver.context.prompt import PromptBuilder, render_context
from contextweaver.routing.cards import ChoiceCard
from contextweaver.types import ContextItem, ItemKind, Phase

# ---------------------------------------------------------------------------
# render_context tests
# ---------------------------------------------------------------------------


def test_render_context_empty() -> None:
    rendered, tokens = render_context([], [], {}, Phase.ANSWER)
    assert rendered == ""
    assert tokens == {}


def test_render_context_single_item() -> None:
    item = ContextItem(id="i1", kind=ItemKind.USER_TURN, text="Hi", token_estimate=1)
    rendered, tokens = render_context([item], [], {}, Phase.ANSWER)
    assert "[USER]" in rendered
    assert "Hi" in rendered
    assert "context_items" in tokens


def test_render_context_multiple_items() -> None:
    items = [
        ContextItem(id="i1", kind=ItemKind.USER_TURN, text="Question", token_estimate=2),
        ContextItem(id="i2", kind=ItemKind.AGENT_MSG, text="Answer", token_estimate=2),
    ]
    rendered, tokens = render_context(items, [], {}, Phase.ANSWER)
    assert "Question" in rendered
    assert "Answer" in rendered
    assert "[USER]" in rendered
    assert "[ASSISTANT]" in rendered


def test_render_context_tool_result_label() -> None:
    item = ContextItem(id="i1", kind=ItemKind.TOOL_RESULT, text="42 rows", token_estimate=2)
    rendered, _tokens = render_context([item], [], {}, Phase.ANSWER)
    assert "TOOL RESULT" in rendered


def test_render_context_with_artifact_ref() -> None:
    item = ContextItem(
        id="i1",
        kind=ItemKind.TOOL_RESULT,
        text="summary",
        token_estimate=2,
        artifact_ref="h1",
    )
    rendered, _tokens = render_context([item], [], {}, Phase.ANSWER)
    assert "artifact:h1" in rendered


def test_render_context_with_facts() -> None:
    rendered, tokens = render_context(
        items=[],
        episodic_summaries=[],
        facts={"user_name": "Alice"},
        phase=Phase.ANSWER,
    )
    assert "## Known Facts" in rendered
    assert "user_name: Alice" in rendered
    assert "facts" in tokens


def test_render_context_with_episodic_summaries() -> None:
    rendered, tokens = render_context(
        items=[],
        episodic_summaries=["User asked about weather"],
        facts={},
        phase=Phase.ANSWER,
    )
    assert "## Recent Context" in rendered
    assert "User asked about weather" in rendered
    assert "episodic" in tokens


def test_render_context_all_sections() -> None:
    items = [
        ContextItem(id="i1", kind=ItemKind.USER_TURN, text="Hello", token_estimate=1),
    ]
    rendered, tokens = render_context(
        items=items,
        episodic_summaries=["prior exchange"],
        facts={"key": "value"},
        phase=Phase.ANSWER,
    )
    assert "## Known Facts" in rendered
    assert "## Recent Context" in rendered
    assert "[USER]" in rendered
    assert "facts" in tokens
    assert "episodic" in tokens
    assert "context_items" in tokens


# ---------------------------------------------------------------------------
# PromptBuilder tests
# ---------------------------------------------------------------------------


def test_prompt_builder_sync_answer_phase() -> None:
    builder = PromptBuilder()

    class FakePack:
        rendered_text = "[USER]\nHello"
        artifacts_available: list[str] = []

    result = builder.build_prompt_sync("Answer the user", Phase.ANSWER, FakePack())
    assert "## Instructions" in result
    assert "## Goal" in result
    assert "Answer the user" in result
    assert "evidence-based" in result


def test_prompt_builder_sync_route_phase() -> None:
    builder = PromptBuilder()

    class FakePack:
        rendered_text = ""
        artifacts_available: list[str] = []

    result = builder.build_prompt_sync("Pick a tool", Phase.ROUTE, FakePack())
    assert "selecting tools" in result.lower() or "Select one or more tools" in result


def test_prompt_builder_sync_with_choice_cards() -> None:
    builder = PromptBuilder()

    class FakePack:
        rendered_text = "context"
        artifacts_available: list[str] = []

    cards = [
        ChoiceCard(id="t1", kind="tool", name="search", description="Search tool"),
    ]
    result = builder.build_prompt_sync("Find tools", Phase.ROUTE, FakePack(), cards)
    assert "Available Tools" in result
    assert "search" in result


def test_prompt_builder_sync_with_artifacts() -> None:
    builder = PromptBuilder()

    class FakePack:
        rendered_text = "some context"
        artifacts_available = ["art1", "art2"]

    result = builder.build_prompt_sync("goal", Phase.ANSWER, FakePack())
    assert "Artifacts Available" in result
    assert "art1" in result
    assert "art2" in result


async def test_prompt_builder_async() -> None:
    builder = PromptBuilder()

    class FakePack:
        rendered_text = "context"
        artifacts_available: list[str] = []

    result = await builder.build_prompt("goal", Phase.ANSWER, FakePack())
    assert isinstance(result, str)
    assert "goal" in result

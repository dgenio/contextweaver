"""Tests for contextweaver.routing.cards -- make_choice_cards limits, render_cards_text format, score omission."""

from __future__ import annotations

from contextweaver.routing.cards import (
    ChoiceCard,
    make_choice_cards,
    render_card_line,
    render_cards_text,
)
from contextweaver.types import SelectableItem


def _item(
    iid: str, description: str = "A tool that does things", tags: list[str] | None = None
) -> SelectableItem:
    return SelectableItem(
        id=iid,
        kind="tool",
        name=iid,
        description=description,
        tags=tags or ["test"],
        namespace="ns",
    )


class TestMakeChoiceCards:
    """Tests for make_choice_cards."""

    def test_basic_conversion(self) -> None:
        items = [_item("t1"), _item("t2"), _item("t3")]
        cards = make_choice_cards(items)
        assert len(cards) == 3
        assert all(isinstance(c, ChoiceCard) for c in cards)
        assert cards[0].id == "t1"

    def test_max_choices_enforced(self) -> None:
        items = [_item(f"t{i}") for i in range(30)]
        cards = make_choice_cards(items, max_choices=5)
        assert len(cards) == 5

    def test_description_truncation(self) -> None:
        long_desc = "A" * 500
        items = [_item("t1", description=long_desc)]
        cards = make_choice_cards(items, max_desc_chars=50)
        assert len(cards[0].description) <= 50
        assert cards[0].description.endswith("...")

    def test_scores_included(self) -> None:
        items = [_item("t1"), _item("t2")]
        scores = {"t1": 0.9, "t2": 0.5}
        cards = make_choice_cards(items, scores=scores)
        assert cards[0].score == 0.9
        assert cards[1].score == 0.5

    def test_score_omission_when_none(self) -> None:
        items = [_item("t1")]
        cards = make_choice_cards(items)
        assert cards[0].score is None
        d = cards[0].to_dict()
        assert "score" not in d

    def test_has_schema_flag(self) -> None:
        item = SelectableItem(
            id="t1",
            kind="tool",
            name="t1",
            description="desc",
            args_schema={"type": "object"},
        )
        cards = make_choice_cards([item])
        assert cards[0].has_schema is True

        item_no_schema = _item("t2")
        cards2 = make_choice_cards([item_no_schema])
        assert cards2[0].has_schema is False

    def test_total_chars_limit_drops_lowest_scored(self) -> None:
        items = [_item(f"t{i}") for i in range(10)]
        scores = {f"t{i}": float(i) / 10 for i in range(10)}
        cards = make_choice_cards(items, scores=scores, max_total_chars=200)
        # Should have dropped some cards to fit within 200 chars
        assert len(cards) < 10
        # Remaining cards should be the highest scored
        remaining_scores = [c.score for c in cards if c.score is not None]
        assert remaining_scores == sorted(remaining_scores)  # ascending order preserved from items


class TestRenderCardsText:
    """Tests for render_cards_text and render_card_line."""

    def test_render_card_line_format(self) -> None:
        card = ChoiceCard(
            id="t1",
            kind="tool",
            name="billing.search",
            description="Search invoices",
            tags=["billing"],
            score=0.82,
        )
        line = render_card_line(card, 0, 5)
        assert "[1/5]" in line
        assert "billing.search" in line
        assert "tool" in line
        assert "Search invoices" in line
        assert "score=0.82" in line

    def test_render_cards_text_multi_line(self) -> None:
        cards = [
            ChoiceCard(id="t1", kind="tool", name="t1", description="desc1"),
            ChoiceCard(id="t2", kind="tool", name="t2", description="desc2"),
        ]
        text = render_cards_text(cards)
        lines = text.strip().split("\n")
        assert len(lines) == 2
        assert "[1/2]" in lines[0]
        assert "[2/2]" in lines[1]

    def test_render_card_without_score(self) -> None:
        card = ChoiceCard(id="t1", kind="tool", name="t1", description="desc")
        line = render_card_line(card, 0, 1)
        assert "score=" not in line

    def test_choice_card_round_trip(self) -> None:
        card = ChoiceCard(
            id="t1",
            kind="tool",
            name="t1",
            description="desc",
            tags=["a", "b"],
            namespace="ns",
            has_schema=True,
            score=0.5,
        )
        d = card.to_dict()
        restored = ChoiceCard.from_dict(d)
        assert restored.id == card.id
        assert restored.score == card.score
        assert restored.has_schema is True

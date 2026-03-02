"""Tests for contextweaver.routing.cards."""

from __future__ import annotations

from contextweaver.envelope import ChoiceCard
from contextweaver.routing.cards import (
    cards_for_route,
    format_card_for_prompt,
    item_to_card,
    make_choice_cards,
    render_cards,
    render_cards_text,
)
from contextweaver.routing.catalog import Catalog
from contextweaver.types import SelectableItem


def _item(
    iid: str,
    description: str = "",
    tags: list[str] | None = None,
    namespace: str = "",
    args_schema: dict | None = None,  # type: ignore[type-arg]
) -> SelectableItem:
    return SelectableItem(
        id=iid,
        kind="tool",
        name=f"tool_{iid}",
        description=description or f"Does {iid}",
        tags=tags or ["test"],
        namespace=namespace,
        args_schema=args_schema or {},
        side_effects=False,
        cost_hint=0.1,
    )


# ------------------------------------------------------------------
# item_to_card
# ------------------------------------------------------------------


def test_item_to_card() -> None:
    item = _item("t1")
    card = item_to_card(item)
    assert card.id == "t1"
    assert card.name == "tool_t1"
    assert card.kind == "tool"
    assert "args_schema" not in card.to_dict()


def test_item_to_card_has_schema_flag() -> None:
    item = _item("t1", args_schema={"type": "object"})
    card = item_to_card(item)
    assert card.has_schema is True


def test_item_to_card_no_schema_flag() -> None:
    item = _item("t1", args_schema={})
    card = item_to_card(item)
    assert card.has_schema is False


def test_item_to_card_preserves_namespace() -> None:
    item = _item("t1", namespace="billing")
    card = item_to_card(item)
    assert card.namespace == "billing"


def test_item_to_card_with_score() -> None:
    item = _item("t1")
    card = item_to_card(item, score=0.85)
    assert card.score == 0.85


# ------------------------------------------------------------------
# render_cards
# ------------------------------------------------------------------


def test_render_cards() -> None:
    items = [_item("t1"), _item("t2")]
    cards = render_cards(items)
    assert len(cards) == 2
    assert cards[0].id == "t1"


# ------------------------------------------------------------------
# make_choice_cards
# ------------------------------------------------------------------


def test_make_choice_cards_default() -> None:
    items = [_item(f"t{i}") for i in range(5)]
    cards = make_choice_cards(items)
    assert len(cards) == 5


def test_make_choice_cards_max_choices() -> None:
    items = [_item(f"t{i}") for i in range(30)]
    cards = make_choice_cards(items, max_choices=10)
    assert len(cards) <= 10


def test_make_choice_cards_preserves_order_no_scores() -> None:
    """Without scores, original input order is preserved when capping."""
    # IDs in reverse alphabetical order
    items = [_item(f"z{i}") for i in range(5)] + [_item(f"a{i}") for i in range(5)]
    cards = make_choice_cards(items, max_choices=5)
    # Should keep first 5 (z0..z4), not alphabetically first
    assert [c.id for c in cards] == [f"z{i}" for i in range(5)]


def test_make_choice_cards_max_desc_chars_clamped() -> None:
    """max_desc_chars < 4 is clamped to 4."""
    items = [_item("t1", description="Hello world")]
    cards = make_choice_cards(items, max_desc_chars=1)
    # Should not crash; description is truncated to 4 chars
    assert len(cards[0].description) <= 4
    assert cards[0].description.endswith("...")


def test_make_choice_cards_truncates_description() -> None:
    long_desc = "A" * 300
    items = [_item("t1", description=long_desc)]
    cards = make_choice_cards(items, max_desc_chars=50)
    assert len(cards[0].description) <= 50
    assert cards[0].description.endswith("...")


def test_make_choice_cards_with_scores() -> None:
    items = [_item("t1"), _item("t2")]
    scores = {"t1": 0.9, "t2": 0.3}
    cards = make_choice_cards(items, scores=scores)
    # Should have scores attached
    score_map = {c.id: c.score for c in cards}
    assert score_map["t1"] == 0.9
    assert score_map["t2"] == 0.3


def test_make_choice_cards_max_total_chars() -> None:
    items = [_item(f"t{i}", description="X" * 100) for i in range(20)]
    scores = {f"t{i}": float(i) for i in range(20)}
    cards = make_choice_cards(items, max_total_chars=500, scores=scores)
    text = render_cards_text(cards)
    assert len(text) <= 500


def test_make_choice_cards_no_schemas_in_output() -> None:
    items = [_item("t1", args_schema={"type": "object", "properties": {"x": {"type": "int"}}})]
    cards = make_choice_cards(items)
    d = cards[0].to_dict()
    assert "args_schema" not in d
    assert d["has_schema"] is True


# ------------------------------------------------------------------
# render_cards_text
# ------------------------------------------------------------------


def test_render_cards_text_format() -> None:
    cards = [
        ChoiceCard(
            id="billing.invoices.search",
            name="search",
            description="Search invoices by date",
            tags=["billing", "search"],
            kind="tool",
            score=0.82,
        ),
    ]
    text = render_cards_text(cards)
    assert "[1/1]" in text
    assert "billing.invoices.search" in text
    assert "(tool)" in text
    assert "score=0.82" in text
    assert "[billing, search]" in text


def test_render_cards_text_no_score() -> None:
    cards = [
        ChoiceCard(
            id="t1",
            name="t1",
            description="desc",
            kind="tool",
            score=None,
        ),
    ]
    text = render_cards_text(cards)
    assert "score=" not in text


def test_render_cards_text_numbering() -> None:
    cards = [
        ChoiceCard(id=f"t{i}", name=f"t{i}", description="d", kind="tool")
        for i in range(3)
    ]
    text = render_cards_text(cards)
    assert "[1/3]" in text
    assert "[2/3]" in text
    assert "[3/3]" in text


def test_render_cards_text_empty() -> None:
    assert render_cards_text([]) == ""


# ------------------------------------------------------------------
# cards_for_route
# ------------------------------------------------------------------


def test_cards_for_route_skips_non_catalog_nodes() -> None:
    catalog = Catalog()
    catalog.register(_item("t1"))
    route = ["ns:data", "t1", "missing_node"]
    cards = cards_for_route(route, catalog)
    assert len(cards) == 1
    assert cards[0].id == "t1"


# ------------------------------------------------------------------
# format_card_for_prompt
# ------------------------------------------------------------------


def test_format_card_for_prompt() -> None:
    card = ChoiceCard(id="c1", name="search", description="Search records", tags=["search"])
    text = format_card_for_prompt(card)
    assert "c1" in text
    assert "search" in text.lower()


def test_format_card_with_side_effects() -> None:
    card = ChoiceCard(id="c2", name="delete", description="Delete record", side_effects=True)
    text = format_card_for_prompt(card)
    assert "side effects" in text.lower()


# ------------------------------------------------------------------
# ChoiceCard serde with new fields
# ------------------------------------------------------------------


def test_choice_card_roundtrip_new_fields() -> None:
    card = ChoiceCard(
        id="t1",
        name="tool1",
        description="desc",
        kind="agent",
        namespace="billing",
        has_schema=True,
        score=0.75,
    )
    restored = ChoiceCard.from_dict(card.to_dict())
    assert restored.kind == "agent"
    assert restored.namespace == "billing"
    assert restored.has_schema is True
    assert restored.score == 0.75


def test_choice_card_score_none_omitted_from_dict() -> None:
    card = ChoiceCard(id="t1", name="t1", description="d", score=None)
    d = card.to_dict()
    assert "score" not in d

"""Tests for contextweaver.routing.cards."""

from __future__ import annotations

from contextweaver.routing.cards import (
    cards_for_route,
    format_card_for_prompt,
    item_to_card,
    render_cards,
)
from contextweaver.routing.catalog import Catalog
from contextweaver.types import ChoiceCard, SelectableItem


def _item(iid: str) -> SelectableItem:
    return SelectableItem(
        id=iid,
        kind="tool",
        name=f"tool_{iid}",
        description=f"Does {iid}",
        tags=["test"],
        side_effects=False,
        cost_hint=0.1,
    )


def test_item_to_card() -> None:
    item = _item("t1")
    card = item_to_card(item)
    assert card.id == "t1"
    assert card.name == "tool_t1"
    assert "args_schema" not in card.to_dict()


def test_render_cards() -> None:
    items = [_item("t1"), _item("t2")]
    cards = render_cards(items)
    assert len(cards) == 2
    assert cards[0].id == "t1"


def test_cards_for_route_skips_non_catalog_nodes() -> None:
    catalog = Catalog()
    catalog.register(_item("t1"))
    route = ["ns:data", "t1", "missing_node"]
    cards = cards_for_route(route, catalog)
    assert len(cards) == 1
    assert cards[0].id == "t1"


def test_format_card_for_prompt() -> None:
    card = ChoiceCard(id="c1", name="search", description="Search records", tags=["search"])
    text = format_card_for_prompt(card)
    assert "c1" in text
    assert "search" in text.lower()


def test_format_card_with_side_effects() -> None:
    card = ChoiceCard(id="c2", name="delete", description="Delete record", side_effects=True)
    text = format_card_for_prompt(card)
    assert "side effects" in text.lower()

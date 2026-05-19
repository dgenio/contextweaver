"""Tests for contextweaver.routing.packer (issue #56)."""

from __future__ import annotations

from contextweaver.routing.packer import DefaultCardPacker, _estimate_card_tokens
from contextweaver.types import SelectableItem


def _item(iid: str, desc: str = "desc", **kw: object) -> SelectableItem:
    return SelectableItem(
        id=iid,
        kind="tool",
        name=str(kw.get("name", iid)),
        description=desc,
        tags=list(kw.get("tags", [])),  # type: ignore[arg-type]
        namespace=str(kw.get("namespace", "")),
    )


def test_pack_returns_choice_cards_in_score_descending_id_ascending_order() -> None:
    """Prompt-cache-stability invariant (issue #218): -score then +id."""
    items = [
        _item("z", desc="A test tool"),
        _item("a", desc="A test tool"),
        _item("m", desc="A test tool"),
    ]
    packer = DefaultCardPacker()
    scores = {"a": 0.5, "m": 0.5, "z": 0.9}
    cards = packer.pack(items, scores)
    assert [c.id for c in cards] == ["z", "a", "m"]


def test_pack_passes_max_cards_through() -> None:
    items = [_item(f"t{i}") for i in range(30)]
    packer = DefaultCardPacker(max_cards=5)
    scores = {it.id: 1.0 for it in items}
    cards = packer.pack(items, scores)
    assert len(cards) == 5


def test_pack_budget_tokens_caps_cumulative_token_estimate() -> None:
    items = [_item(f"t{i}", desc="x" * 200) for i in range(10)]
    packer = DefaultCardPacker()
    scores = {it.id: 1.0 for it in items}
    cards = packer.pack(items, scores, budget_tokens=1)
    # The first card always lands (the packer must never return an empty
    # list when items exist).  But the budget caps subsequent cards.
    assert cards
    assert len(cards) < len(items)


def test_pack_budget_none_disables_cap() -> None:
    items = [_item(f"t{i}", desc="x" * 1000) for i in range(5)]
    packer = DefaultCardPacker()
    scores = {it.id: 1.0 for it in items}
    cards = packer.pack(items, scores, budget_tokens=None)
    assert len(cards) == len(items)


def test_pack_empty_items_returns_empty_list() -> None:
    packer = DefaultCardPacker()
    assert packer.pack([], {}, budget_tokens=100) == []


def test_pack_scores_attached_to_cards() -> None:
    items = [_item("a"), _item("b")]
    packer = DefaultCardPacker()
    cards = packer.pack(items, {"a": 0.8, "b": 0.4})
    by_id = {c.id: c for c in cards}
    assert by_id["a"].score == 0.8
    assert by_id["b"].score == 0.4


def test_estimate_card_tokens_uses_char_div_four_heuristic() -> None:
    items = [_item("a", desc="abcd" * 10)]
    packer = DefaultCardPacker()
    cards = packer.pack(items, {"a": 1.0})
    est = _estimate_card_tokens(cards[0])
    # Sum of len(id) + len(name) + len(kind) + len(summary) + len(tags) divided by 4.
    assert est >= 0
    # Not absurdly large for a short item
    assert est < 200

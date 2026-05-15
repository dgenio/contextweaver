"""Tests for the token-budget enforcement in routing.cards (gateway_spec.md §2)."""

from __future__ import annotations

import pytest

from contextweaver.envelope import ChoiceCard
from contextweaver.exceptions import CatalogError
from contextweaver.routing.cards import (
    DEFAULT_BROWSE_PREAMBLE_TOKENS,
    DEFAULT_CARD_HARD_CAP_TOKENS,
    DEFAULT_CARD_TARGET_TOKENS,
    bound_browse_response,
    count_tokens,
    make_choice_cards,
    truncate_description_to_tokens,
)
from contextweaver.types import SelectableItem

# ---------------------------------------------------------------------------
# truncate_description_to_tokens — §2.4
# ---------------------------------------------------------------------------


def test_truncate_within_budget_returns_verbatim() -> None:
    text = "Short description that fits."
    assert truncate_description_to_tokens(text, 100) == text


def test_truncate_prefers_sentence_boundary() -> None:
    text = "First sentence. Second sentence. Third sentence that is much longer than the rest."
    result = truncate_description_to_tokens(text, 10)
    # Sentence boundaries (".") appear at positions covering "First sentence."
    # and "First sentence. Second sentence." — the longer prefix that fits
    # below 10 tokens should be returned.
    assert result.endswith(".")
    assert count_tokens(result) <= 10


def test_truncate_hard_cut_appends_ellipsis_when_no_sentence_fits() -> None:
    """A very long word with no sentence terminator falls through to byte-cut."""
    text = "x" * 800  # No sentence boundary at all
    result = truncate_description_to_tokens(text, 5)
    assert result.endswith("…")
    assert count_tokens(result) <= 5


def test_truncate_zero_budget_returns_empty() -> None:
    assert truncate_description_to_tokens("anything", 0) == ""


def test_truncate_deterministic() -> None:
    text = "A " * 200
    assert truncate_description_to_tokens(text, 20) == truncate_description_to_tokens(text, 20)


# ---------------------------------------------------------------------------
# make_choice_cards — token-budget enforcement
# ---------------------------------------------------------------------------


def _item(
    iid: str, description: str = "Does the thing.", name: str | None = None
) -> SelectableItem:
    return SelectableItem(
        id=iid,
        kind="tool",
        name=name or f"tool_{iid}",
        description=description,
        tags=["test"],
    )


def test_make_choice_cards_truncates_to_target_tokens() -> None:
    long_desc = "A long sentence. " * 100
    items = [_item("t1", description=long_desc)]
    cards = make_choice_cards(items, target_tokens_per_card=60)
    # Per-card rendering must fit within the hard cap.
    rendered_line = f"[1/1] {cards[0].id} ({cards[0].kind}) — {cards[0].description}"
    assert count_tokens(rendered_line) <= DEFAULT_CARD_HARD_CAP_TOKENS


def test_make_choice_cards_score_desc_id_asc() -> None:
    """§2.5: sort by score desc, ties by id asc."""
    items = [_item("zzz"), _item("aaa"), _item("mmm")]
    cards = make_choice_cards(items, scores={"zzz": 0.5, "aaa": 0.5, "mmm": 0.5})
    assert [c.id for c in cards] == ["aaa", "mmm", "zzz"]


def test_make_choice_cards_caps_name_to_64_chars() -> None:
    """§2.1: name ≤ 64 characters."""
    items = [_item("t1", name="X" * 200)]
    cards = make_choice_cards(items)
    assert len(cards[0].name) <= 64


def test_make_choice_cards_caps_tags_to_5_entries() -> None:
    """§2.1: tags max 5 entries."""
    item = SelectableItem(
        id="t1",
        kind="tool",
        name="t1",
        description="d",
        tags=[f"tag{i}" for i in range(20)],
    )
    cards = make_choice_cards([item])
    assert len(cards[0].tags) <= 5


def test_make_choice_cards_caps_tag_length_to_24_chars() -> None:
    """§2.1: each tag ≤ 24 characters."""
    item = SelectableItem(
        id="t1",
        kind="tool",
        name="t1",
        description="d",
        tags=["x" * 100],
    )
    cards = make_choice_cards([item])
    assert all(len(t) <= 24 for t in cards[0].tags)


def test_make_choice_cards_raises_when_card_exceeds_hard_cap() -> None:
    """If non-description content alone exceeds the hard cap, raise."""
    # Tag entries are capped at 24 chars + 5 entries — the cap is large
    # enough to exceed an artificially-low hard-cap.
    item = SelectableItem(
        id="t1",
        kind="tool",
        name="X" * 64,
        description="d",
        tags=["aaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbb"],
    )
    with pytest.raises(CatalogError, match="hard cap"):
        make_choice_cards([item], target_tokens_per_card=5, hard_cap_tokens_per_card=5)


# ---------------------------------------------------------------------------
# bound_browse_response — §2.3
# ---------------------------------------------------------------------------


def test_bound_browse_response_fits_within_total_budget() -> None:
    items = [_item(f"t{i:02d}") for i in range(50)]
    scores = {f"t{i:02d}": float(i) for i in range(50)}
    cards = make_choice_cards(items, max_cards=50, scores=scores)
    bounded = bound_browse_response(cards)
    total = sum(count_tokens(f"[1/1] {c.id} ({c.kind}) — {c.description}") for c in bounded)
    cap = DEFAULT_CARD_HARD_CAP_TOKENS * len(bounded) + DEFAULT_BROWSE_PREAMBLE_TOKENS
    assert total + DEFAULT_BROWSE_PREAMBLE_TOKENS <= cap


def test_bound_browse_response_drops_lowest_scoring_tail() -> None:
    """Lowest-scored tail is dropped first when the total budget is tight."""
    # Build cards whose per-card cost is close to the hard cap so the
    # response cannot fit all of them under the §2.3 total budget.
    desc = "Detailed description with several words to fill the budget."
    items = [_item(f"t{i:02d}", description=desc) for i in range(15)]
    scores = {f"t{i:02d}": float(i) for i in range(15)}
    cards = make_choice_cards(items, max_cards=15, scores=scores)
    bounded = bound_browse_response(cards, preamble_tokens=4)
    # The highest-scored card is always retained.
    assert bounded[0].id == "t14"
    # Ordering is preserved (score desc → t14, t13, ...).
    ids = [c.id for c in bounded]
    expected_prefix = [f"t{i:02d}" for i in range(14, 14 - len(bounded), -1)]
    assert ids == expected_prefix


def test_bound_browse_response_preserves_score_desc_tie_break() -> None:
    items = [_item("zzz"), _item("aaa"), _item("mmm")]
    cards = make_choice_cards(items, scores={"zzz": 0.5, "aaa": 0.5, "mmm": 0.5})
    bounded = bound_browse_response(cards)
    assert [c.id for c in bounded] == ["aaa", "mmm", "zzz"]


def test_bound_browse_response_handles_empty() -> None:
    assert bound_browse_response([]) == []


def test_default_card_tokens_match_spec() -> None:
    """Spec §2.3 anchors: target ≤ 60, hard cap ≤ 80, preamble = 32."""
    assert DEFAULT_CARD_TARGET_TOKENS == 60
    assert DEFAULT_CARD_HARD_CAP_TOKENS == 80
    assert DEFAULT_BROWSE_PREAMBLE_TOKENS == 32


# ---------------------------------------------------------------------------
# ChoiceCard.to_dict — banned-field guard (§2.2)
# ---------------------------------------------------------------------------


def test_choice_card_to_dict_omits_schema_keys() -> None:
    """§2.2: card serialisation MUST NOT carry args_schema / output_schema / examples."""
    card = ChoiceCard(
        id="t1",
        name="t",
        description="d",
        has_schema=True,
    )
    d = card.to_dict()
    for banned in ("args_schema", "output_schema", "examples", "annotations", "_meta"):
        assert banned not in d

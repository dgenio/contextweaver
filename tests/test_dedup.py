"""Tests for contextweaver.context.dedup."""

from __future__ import annotations

from contextweaver.context.dedup import deduplicate_candidates
from contextweaver.types import ContextItem, ItemKind


def _item(iid: str, text: str) -> ContextItem:
    return ContextItem(id=iid, kind=ItemKind.user_turn, text=text)


def test_no_duplicates_keeps_all() -> None:
    items = [
        (1.0, _item("i1", "search the database")),
        (0.9, _item("i2", "send notification email")),
        (0.8, _item("i3", "compute statistics")),
    ]
    kept, removed = deduplicate_candidates(items)
    assert len(kept) == 3
    assert removed == 0


def test_exact_duplicate_removed() -> None:
    text = "the quick brown fox jumps over the lazy dog"
    items = [
        (1.0, _item("i1", text)),
        (0.9, _item("i2", text)),
    ]
    kept, removed = deduplicate_candidates(items, similarity_threshold=0.85)
    assert len(kept) == 1
    assert removed == 1
    # First (higher score) should be kept
    assert kept[0][1].id == "i1"


def test_near_duplicate_removed() -> None:
    items = [
        (1.0, _item("i1", "search database records quickly using query")),
        (0.9, _item("i2", "search database records quickly using queries")),
    ]
    kept, removed = deduplicate_candidates(items, similarity_threshold=0.7)
    assert removed >= 1


def test_threshold_one_keeps_all_unless_identical() -> None:
    items = [
        (1.0, _item("i1", "alpha beta gamma")),
        (0.9, _item("i2", "alpha beta delta")),
    ]
    kept, removed = deduplicate_candidates(items, similarity_threshold=1.0)
    # Jaccard < 1.0 so both kept
    assert len(kept) == 2
    assert removed == 0


def test_custom_threshold_less_aggressive() -> None:
    """A higher threshold keeps near-duplicates that the default would remove."""
    # Jaccard similarity ≈ 0.89 — above 0.85 default but below 0.99.
    text_a = (
        "alpha bravo charlie delta echo foxtrot golf hotel"
        " india juliet kilo lima mike november oscar papa query"
    )
    text_b = (
        "alpha bravo charlie delta echo foxtrot golf hotel"
        " india juliet kilo lima mike november oscar papa quebec"
    )
    items = [
        (1.0, _item("i1", text_a)),
        (0.9, _item("i2", text_b)),
    ]
    # Default (0.85) should remove the near-duplicate (0.89 >= 0.85)
    _, removed_default = deduplicate_candidates(items, similarity_threshold=0.85)
    # Very high threshold keeps both (0.89 < 0.99)
    kept_high, removed_high = deduplicate_candidates(items, similarity_threshold=0.99)
    assert removed_high == 0
    assert len(kept_high) == 2
    # Default must be strictly more aggressive
    assert removed_default > removed_high

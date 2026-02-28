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

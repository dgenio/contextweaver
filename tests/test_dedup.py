"""Tests for contextweaver.context.dedup -- content hash dedup, count."""

from __future__ import annotations

from contextweaver.context.dedup import deduplicate_candidates
from contextweaver.types import ContextItem, ItemKind


def _item(iid: str, text: str) -> ContextItem:
    return ContextItem(id=iid, kind=ItemKind.USER_TURN, text=text, token_estimate=len(text) // 4)


class TestDeduplicateCandidates:
    """Tests for deduplicate_candidates."""

    def test_no_duplicates_keeps_all(self) -> None:
        scored = [
            (_item("i1", "search the database"), 1.0),
            (_item("i2", "send notification email"), 0.9),
            (_item("i3", "compute statistics"), 0.8),
        ]
        kept, removed = deduplicate_candidates(scored)
        assert len(kept) == 3
        assert removed == 0

    def test_exact_duplicate_removed(self) -> None:
        text = "the quick brown fox jumps over the lazy dog"
        scored = [
            (_item("i1", text), 1.0),
            (_item("i2", text), 0.9),
        ]
        kept, removed = deduplicate_candidates(scored)
        assert len(kept) == 1
        assert removed == 1
        # Higher-scored item (first in list) should be kept
        assert kept[0][0].id == "i1"

    def test_different_text_not_deduped(self) -> None:
        scored = [
            (_item("i1", "alpha beta gamma"), 1.0),
            (_item("i2", "alpha beta delta"), 0.9),
        ]
        kept, removed = deduplicate_candidates(scored)
        # MD5-based dedup: different text -> different hash -> both kept
        assert len(kept) == 2
        assert removed == 0

    def test_multiple_duplicates(self) -> None:
        text = "duplicate content here"
        scored = [
            (_item("i1", text), 1.0),
            (_item("i2", text), 0.9),
            (_item("i3", text), 0.8),
            (_item("i4", "unique content"), 0.7),
        ]
        kept, removed = deduplicate_candidates(scored)
        assert len(kept) == 2
        assert removed == 2
        ids = {item.id for item, _ in kept}
        assert "i1" in ids
        assert "i4" in ids

    def test_empty_input(self) -> None:
        kept, removed = deduplicate_candidates([])
        assert kept == []
        assert removed == 0

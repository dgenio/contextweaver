"""Tests for contextweaver.routing.labeler."""

from __future__ import annotations

from contextweaver.routing.labeler import KeywordLabeler
from contextweaver.types import SelectableItem


def _item(name: str, description: str, tags: list[str] | None = None) -> SelectableItem:
    return SelectableItem(id=name, kind="tool", name=name, description=description, tags=tags or [])


def test_data_category() -> None:
    labeler = KeywordLabeler()
    item = _item("db_read", "Read from database", tags=["data"])
    cat, conf = labeler.label(item)
    assert cat == "data"


def test_search_category() -> None:
    labeler = KeywordLabeler()
    item = _item("search_tool", "Search and find records in the index")
    cat, conf = labeler.label(item)
    assert cat == "search"


def test_general_fallback() -> None:
    labeler = KeywordLabeler()
    item = _item("xyz123", "Does something obscure")
    cat, conf = labeler.label(item)
    assert cat == "general"


def test_empty_item() -> None:
    labeler = KeywordLabeler()
    item = _item("", "")
    cat, conf = labeler.label(item)
    assert cat == "general"
    assert conf == "none"


def test_confidence_levels() -> None:
    labeler = KeywordLabeler()
    # Many search keywords → high confidence
    item = _item("s", "search find lookup retrieve index browse")
    _, conf = labeler.label(item)
    assert conf in ("high", "medium", "low")

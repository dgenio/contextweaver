"""Tests for contextweaver.routing.labeler."""

from __future__ import annotations

from contextweaver.routing.labeler import KeywordLabeler
from contextweaver.types import SelectableItem


def _item(
    name: str,
    description: str,
    tags: list[str] | None = None,
    namespace: str = "",
) -> SelectableItem:
    return SelectableItem(
        id=name,
        kind="tool",
        name=name,
        description=description,
        tags=tags or [],
        namespace=namespace,
    )


# ------------------------------------------------------------------
# Single-item labeling (Labeler protocol)
# ------------------------------------------------------------------


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


def test_communication_category() -> None:
    labeler = KeywordLabeler()
    item = _item("mailer", "Send email notifications via webhook", tags=["email"])
    cat, _ = labeler.label(item)
    assert cat == "communication"


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
    item = _item("s", "search find lookup retrieve index browse")
    _, conf = labeler.label(item)
    assert conf in ("high", "medium", "low")


# ------------------------------------------------------------------
# Group labeling (label_group)
# ------------------------------------------------------------------


def test_label_group_empty() -> None:
    labeler = KeywordLabeler()
    label, hint = labeler.label_group([])
    assert label == "general"
    assert "general" in hint


def test_label_group_top_k_tokens() -> None:
    labeler = KeywordLabeler(top_k=2)
    items = [
        _item("t1", "search database records"),
        _item("t2", "search database entries"),
    ]
    label, hint = labeler.label_group(items)
    # "search" and "database" should be top tokens
    assert "search" in label or "database" in label
    assert "Tools related to" in hint


def test_label_group_namespace_prepended() -> None:
    labeler = KeywordLabeler(namespace_threshold=0.6)
    items = [
        _item("t1", "tool a", namespace="billing"),
        _item("t2", "tool b", namespace="billing"),
        _item("t3", "tool c", namespace="billing"),
    ]
    label, _ = labeler.label_group(items)
    assert label.startswith("billing:")


def test_label_group_namespace_below_threshold() -> None:
    labeler = KeywordLabeler(namespace_threshold=0.8)
    items = [
        _item("t1", "tool a", namespace="billing"),
        _item("t2", "tool b", namespace="crm"),
        _item("t3", "tool c", namespace="billing"),
    ]
    label, _ = labeler.label_group(items)
    # 2/3 = 0.67 < 0.8, namespace should NOT be prepended
    assert not label.startswith("billing:") and not label.startswith("crm:")


def test_label_group_routing_hint_format() -> None:
    labeler = KeywordLabeler()
    items = [_item("t1", "calculate math results", tags=["compute"])]
    _, hint = labeler.label_group(items)
    assert hint.startswith("Tools related to")

"""Tests for contextweaver.routing.normalizer (issue #44)."""

from __future__ import annotations

import pytest

from contextweaver.exceptions import CatalogError
from contextweaver.routing.normalizer import CatalogNormalizer, NormalizationReport
from contextweaver.types import SelectableItem


def _item(
    iid: str,
    *,
    name: str = "",
    description: str = "desc",
    namespace: str = "",
    tags: list[str] | None = None,
) -> SelectableItem:
    return SelectableItem(
        id=iid,
        kind="tool",
        name=name or iid,
        description=description,
        namespace=namespace,
        tags=tags or [],
    )


def test_dedup_tags_case_insensitive() -> None:
    item = _item("a", tags=["Email", "email", "  EMAIL "])
    out, report = CatalogNormalizer().normalize([item])
    assert out[0].tags == ["email"]
    assert report.tag_dedup_count == 1


def test_tags_sorted() -> None:
    item = _item("a", tags=["zeta", "alpha", "mu"])
    out, _ = CatalogNormalizer().normalize([item])
    assert out[0].tags == ["alpha", "mu", "zeta"]


def test_lowercase_tags_disabled() -> None:
    item = _item("a", tags=["Email", "WEB", "email"])
    out, _ = CatalogNormalizer(lowercase_tags=False).normalize([item])
    # "Email" and "email" remain distinct when case-sensitive.
    assert "Email" in out[0].tags
    assert "email" in out[0].tags
    assert "WEB" in out[0].tags


def test_whitespace_collapsed_in_description() -> None:
    item = _item("a", description="multiple    spaces\n\nhere")
    out, report = CatalogNormalizer().normalize([item])
    assert out[0].description == "multiple spaces here"
    assert report.whitespace_normalized_count == 1


def test_namespace_stripped_of_trailing_dot() -> None:
    item = _item("a", namespace="billing.")
    out, _ = CatalogNormalizer().normalize([item])
    assert out[0].namespace == "billing"


def test_empty_description_filled_from_name() -> None:
    item = _item("a", name="my tool", description="")
    out, report = CatalogNormalizer().normalize([item])
    assert out[0].description == "my tool"
    assert report.description_filled_count == 1


def test_invalid_id_lenient_drops_item() -> None:
    items = [_item("a"), _item("")]
    out, report = CatalogNormalizer().normalize(items)
    assert len(out) == 1
    assert out[0].id == "a"
    assert "" in report.invalid_ids


def test_duplicate_id_lenient_drops_second() -> None:
    items = [_item("a"), _item("a")]
    out, report = CatalogNormalizer().normalize(items)
    assert len(out) == 1
    assert "a" in report.invalid_ids


def test_strict_raises_on_blank_id() -> None:
    with pytest.raises(CatalogError, match="empty id"):
        CatalogNormalizer(strict=True).normalize([_item("")])


def test_strict_raises_on_duplicate_id() -> None:
    with pytest.raises(CatalogError, match="Duplicate"):
        CatalogNormalizer(strict=True).normalize([_item("a"), _item("a")])


def test_input_not_mutated() -> None:
    """The normalizer must not modify input objects in place."""
    item = _item("a", tags=["Email", "email"])
    original_tags = list(item.tags)
    CatalogNormalizer().normalize([item])
    assert item.tags == original_tags


def test_report_aggregates_changes() -> None:
    items = [
        _item("a", tags=["X", "x"], description="  spaced  "),
        _item("b", tags=["c"], description=""),
        _item("c", tags=["already", "sorted"], description="clean"),
    ]
    _, report = CatalogNormalizer().normalize(items)
    assert report.items_processed == 3
    assert report.tag_dedup_count == 1  # only item a has dedupable tags
    assert report.description_filled_count == 1  # item b
    assert report.whitespace_normalized_count >= 1


def test_report_to_dict() -> None:
    report = NormalizationReport(items_processed=5, tag_dedup_count=2)
    d = report.to_dict()
    assert d["items_processed"] == 5
    assert d["tag_dedup_count"] == 2
    assert d["invalid_ids"] == []


def test_changed_count() -> None:
    report = NormalizationReport(
        items_processed=10,
        tag_dedup_count=3,
        description_filled_count=2,
        whitespace_normalized_count=1,
    )
    assert report.changed_count == 6

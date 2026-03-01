"""Tests for contextweaver.routing.catalog."""

from __future__ import annotations

import pytest

from contextweaver.exceptions import CatalogError, ItemNotFoundError
from contextweaver.routing.catalog import Catalog
from contextweaver.types import SelectableItem


def _item(iid: str, tags: list[str] | None = None, namespace: str = "") -> SelectableItem:
    return SelectableItem(
        id=iid,
        kind="tool",
        name=iid,
        description=f"desc {iid}",
        tags=tags or [],
        namespace=namespace,
    )


def test_register_and_get() -> None:
    catalog = Catalog()
    catalog.register(_item("t1"))
    assert catalog.get("t1").id == "t1"


def test_duplicate_raises() -> None:
    catalog = Catalog()
    catalog.register(_item("t1"))
    with pytest.raises(CatalogError):
        catalog.register(_item("t1"))


def test_get_missing_raises() -> None:
    catalog = Catalog()
    with pytest.raises(ItemNotFoundError):
        catalog.get("missing")


def test_all_sorted() -> None:
    catalog = Catalog()
    catalog.register(_item("z1"))
    catalog.register(_item("a1"))
    ids = [i.id for i in catalog.all()]
    assert ids == ["a1", "z1"]


def test_filter_by_namespace() -> None:
    catalog = Catalog()
    catalog.register(_item("t1", namespace="ns1"))
    catalog.register(_item("t2", namespace="ns2"))
    catalog.register(_item("t3", namespace="ns1"))
    results = catalog.filter_by_namespace("ns1")
    assert {r.id for r in results} == {"t1", "t3"}


def test_filter_by_tags() -> None:
    catalog = Catalog()
    catalog.register(_item("t1", tags=["data", "search"]))
    catalog.register(_item("t2", tags=["data"]))
    catalog.register(_item("t3", tags=["compute"]))
    results = catalog.filter_by_tags("data", "search")
    assert [r.id for r in results] == ["t1"]


def test_roundtrip() -> None:
    catalog = Catalog()
    catalog.register(_item("t1", tags=["a"]))
    restored = Catalog.from_dict(catalog.to_dict())
    assert restored.get("t1").tags == ["a"]

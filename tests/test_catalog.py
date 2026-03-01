"""Tests for contextweaver.routing.catalog."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from contextweaver.exceptions import CatalogError, ItemNotFoundError
from contextweaver.routing.catalog import (
    Catalog,
    generate_sample_catalog,
    load_catalog_dicts,
    load_catalog_json,
)
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


# ------------------------------------------------------------------
# Catalog class
# ------------------------------------------------------------------


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


# ------------------------------------------------------------------
# load_catalog_json
# ------------------------------------------------------------------


def test_load_catalog_json() -> None:
    data = [
        {"id": "t1", "kind": "tool", "name": "t1", "description": "desc"},
        {"id": "t2", "kind": "tool", "name": "t2", "description": "desc"},
    ]
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(data, f)
        path = f.name
    try:
        items = load_catalog_json(path)
        assert len(items) == 2
        assert items[0].id == "t1"
    finally:
        Path(path).unlink()


def test_load_catalog_json_missing_file() -> None:
    with pytest.raises(CatalogError, match="Cannot read"):
        load_catalog_json("/nonexistent/path.json")


def test_load_catalog_json_invalid_json() -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        f.write("{not valid json")
        path = f.name
    try:
        with pytest.raises(CatalogError, match="Invalid JSON"):
            load_catalog_json(path)
    finally:
        Path(path).unlink()


def test_load_catalog_json_not_array() -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump({"items": []}, f)
        path = f.name
    try:
        with pytest.raises(CatalogError, match="must be an array"):
            load_catalog_json(path)
    finally:
        Path(path).unlink()


# ------------------------------------------------------------------
# load_catalog_dicts
# ------------------------------------------------------------------


def test_load_catalog_dicts_valid() -> None:
    data = [
        {"id": "t1", "kind": "tool", "name": "t1", "description": "d1"},
    ]
    items = load_catalog_dicts(data)
    assert len(items) == 1
    assert items[0].id == "t1"


def test_load_catalog_dicts_missing_fields() -> None:
    data = [{"id": "t1"}]  # missing kind, name, description
    with pytest.raises(CatalogError, match="missing required"):
        load_catalog_dicts(data)


def test_load_catalog_dicts_not_dict_item() -> None:
    data = ["not a dict"]  # type: ignore[list-item]
    with pytest.raises(CatalogError, match="not a dict"):
        load_catalog_dicts(data)


# ------------------------------------------------------------------
# generate_sample_catalog
# ------------------------------------------------------------------


def test_generate_sample_catalog_default() -> None:
    catalog = generate_sample_catalog()
    assert len(catalog) == 80
    # All items should be dicts with required keys
    for item in catalog:
        assert "id" in item
        assert "kind" in item
        assert "name" in item
        assert "description" in item


def test_generate_sample_catalog_deterministic() -> None:
    c1 = generate_sample_catalog(n=20, seed=42)
    c2 = generate_sample_catalog(n=20, seed=42)
    assert c1 == c2


def test_generate_sample_catalog_different_seeds() -> None:
    c1 = generate_sample_catalog(n=20, seed=1)
    c2 = generate_sample_catalog(n=20, seed=2)
    ids1 = {d["id"] for d in c1}
    ids2 = {d["id"] for d in c2}
    # Different seeds should produce different selections
    assert ids1 != ids2


def test_generate_sample_catalog_sorted_by_id() -> None:
    catalog = generate_sample_catalog(n=40, seed=123)
    ids = [d["id"] for d in catalog]
    assert ids == sorted(ids)


def test_generate_sample_catalog_six_namespaces() -> None:
    catalog = generate_sample_catalog(n=80, seed=42)
    namespaces = {d["namespace"] for d in catalog}
    assert len(namespaces) >= 6


def test_generate_sample_catalog_loadable() -> None:
    data = generate_sample_catalog(n=10, seed=42)
    items = load_catalog_dicts(data)
    assert len(items) == 10

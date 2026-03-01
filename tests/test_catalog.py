"""Tests for contextweaver.routing.catalog -- load_catalog_json, load_catalog_dicts, generate_sample_catalog, CatalogError."""

from __future__ import annotations

import json
import tempfile

import pytest

from contextweaver.exceptions import CatalogError
from contextweaver.routing.catalog import (
    generate_sample_catalog,
    load_catalog_dicts,
    load_catalog_json,
)
from contextweaver.types import SelectableItem


class TestLoadCatalogDicts:
    """Tests for load_catalog_dicts."""

    def test_valid_dicts(self) -> None:
        dicts = [
            {"id": "t1", "kind": "tool", "name": "t1", "description": "desc1"},
            {"id": "t2", "kind": "tool", "name": "t2", "description": "desc2"},
        ]
        items = load_catalog_dicts(dicts)
        assert len(items) == 2
        assert all(isinstance(i, SelectableItem) for i in items)

    def test_missing_required_field_raises(self) -> None:
        dicts = [{"id": "t1", "kind": "tool", "name": "t1"}]  # missing description
        with pytest.raises(CatalogError, match="missing required field"):
            load_catalog_dicts(dicts)

    def test_duplicate_id_raises(self) -> None:
        dicts = [
            {"id": "t1", "kind": "tool", "name": "t1", "description": "desc1"},
            {"id": "t1", "kind": "tool", "name": "t1", "description": "desc2"},
        ]
        with pytest.raises(CatalogError, match="Duplicate"):
            load_catalog_dicts(dicts)

    def test_empty_list(self) -> None:
        items = load_catalog_dicts([])
        assert items == []

    def test_preserves_optional_fields(self) -> None:
        dicts = [
            {
                "id": "t1",
                "kind": "tool",
                "name": "t1",
                "description": "desc",
                "tags": ["a", "b"],
                "namespace": "ns",
            },
        ]
        items = load_catalog_dicts(dicts)
        assert items[0].tags == ["a", "b"]
        assert items[0].namespace == "ns"


class TestLoadCatalogJson:
    """Tests for load_catalog_json."""

    def test_valid_json_file(self) -> None:
        dicts = [
            {"id": "t1", "kind": "tool", "name": "t1", "description": "desc"},
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(dicts, f)
            f.flush()
            items = load_catalog_json(f.name)
        assert len(items) == 1

    def test_invalid_json_raises(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not json")
            f.flush()
            with pytest.raises(CatalogError, match="Failed to load"):
                load_catalog_json(f.name)

    def test_non_list_json_raises(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"key": "value"}, f)
            f.flush()
            with pytest.raises(CatalogError, match="must be a list"):
                load_catalog_json(f.name)

    def test_missing_file_raises(self) -> None:
        with pytest.raises(CatalogError, match="Failed to load"):
            load_catalog_json("/tmp/nonexistent_catalog_file_xyz.json")


class TestGenerateSampleCatalog:
    """Tests for generate_sample_catalog."""

    def test_deterministic(self) -> None:
        c1 = generate_sample_catalog(n=40, seed=42)
        c2 = generate_sample_catalog(n=40, seed=42)
        assert c1 == c2

    def test_different_seeds(self) -> None:
        c1 = generate_sample_catalog(n=40, seed=42)
        c2 = generate_sample_catalog(n=40, seed=99)
        assert c1 != c2

    def test_correct_count(self) -> None:
        catalog = generate_sample_catalog(n=30, seed=1)
        assert len(catalog) == 30

    def test_items_are_valid_dicts(self) -> None:
        catalog = generate_sample_catalog(n=10, seed=42)
        for item in catalog:
            assert "id" in item
            assert "kind" in item
            assert "name" in item
            assert "description" in item

    def test_multiple_namespaces(self) -> None:
        catalog = generate_sample_catalog(n=80, seed=42)
        namespaces = {item["namespace"] for item in catalog}
        assert len(namespaces) >= 5

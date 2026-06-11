"""Tests for contextweaver.routing.catalog."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from contextweaver.envelope import HydrationResult
from contextweaver.exceptions import CatalogError, CatalogValidationError, ItemNotFoundError
from contextweaver.routing.catalog import (
    Catalog,
    CatalogValidationReport,
    ReferenceFinding,
    generate_sample_catalog,
    load_catalog,
    load_catalog_dicts,
    load_catalog_json,
    load_catalog_yaml,
    validate_references,
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


# ------------------------------------------------------------------
# validate_dependencies (issue #27 phase 2)
# ------------------------------------------------------------------


def test_validate_dependencies_returns_empty_for_consistent_catalog() -> None:
    catalog = Catalog()
    catalog.register(_item("auth"))
    catalog.register(
        SelectableItem(
            id="send_email",
            kind="tool",
            name="send_email",
            description="email",
            depends_on=["auth"],
        )
    )
    assert catalog.validate_dependencies() == []


def test_validate_dependencies_warns_on_unknown_reference() -> None:
    catalog = Catalog()
    catalog.register(
        SelectableItem(
            id="send_email",
            kind="tool",
            name="send_email",
            description="email",
            depends_on=["does_not_exist"],
        )
    )
    warnings = catalog.validate_dependencies()
    assert len(warnings) == 1
    assert "send_email" in warnings[0]
    assert "does_not_exist" in warnings[0]


def test_validate_dependencies_warns_per_missing_reference() -> None:
    catalog = Catalog()
    catalog.register(
        SelectableItem(
            id="send_email",
            kind="tool",
            name="send_email",
            description="email",
            depends_on=["missing_a", "missing_b"],
        )
    )
    warnings = catalog.validate_dependencies()
    assert len(warnings) == 2


def test_roundtrip() -> None:
    catalog = Catalog()
    catalog.register(_item("t1", tags=["a"]))
    restored = Catalog.from_dict(catalog.to_dict())
    assert restored.get("t1").tags == ["a"]


# ------------------------------------------------------------------
# validate_references / load on_invalid policy (issue #519)
# ------------------------------------------------------------------


def _dep_item(iid: str, depends_on: list[str]) -> SelectableItem:
    return SelectableItem(
        id=iid, kind="tool", name=iid, description=f"desc {iid}", depends_on=depends_on
    )


def test_validate_references_clean_catalog_is_ok() -> None:
    items = [_item("auth"), _dep_item("send_email", ["auth"])]
    report = validate_references(items)
    assert isinstance(report, CatalogValidationReport)
    assert report.ok
    assert report.findings == []
    assert report.items_processed == 2


def test_validate_references_reports_missing_depends_on() -> None:
    items = [_dep_item("send_email", ["does_not_exist"])]
    report = validate_references(items)
    assert not report.ok
    assert report.findings == [ReferenceFinding("send_email", "depends_on", "does_not_exist")]
    assert "does_not_exist" in report.messages()[0]


def test_validate_references_reports_unsatisfied_requires() -> None:
    needs = SelectableItem(
        id="reporter", kind="tool", name="reporter", description="d", requires=["pdf"]
    )
    report = validate_references([needs])
    assert report.findings == [ReferenceFinding("reporter", "requires", "pdf")]


def test_validate_references_requires_satisfied_by_provides() -> None:
    provider = SelectableItem(
        id="renderer", kind="tool", name="renderer", description="d", provides=["pdf"]
    )
    needs = SelectableItem(
        id="reporter", kind="tool", name="reporter", description="d", requires=["pdf"]
    )
    assert validate_references([provider, needs]).ok


def test_validate_references_findings_are_sorted_deterministically() -> None:
    items = [
        _dep_item("zeta", ["missing_b", "missing_a"]),
        _dep_item("alpha", ["missing_c"]),
    ]
    report = validate_references(items)
    assert [(f.item_id, f.missing) for f in report.findings] == [
        ("alpha", "missing_c"),
        ("zeta", "missing_a"),
        ("zeta", "missing_b"),
    ]
    assert report.to_dict()["findings"][0] == {
        "item_id": "alpha",
        "field": "depends_on",
        "missing": "missing_c",
    }


def test_catalog_validate_references_method() -> None:
    catalog = Catalog()
    catalog.register(_dep_item("send_email", ["does_not_exist"]))
    report = catalog.validate_references()
    assert not report.ok
    assert report.findings[0].missing == "does_not_exist"


def test_load_catalog_dicts_warn_is_default_and_returns_items(
    caplog: pytest.LogCaptureFixture,
) -> None:
    data = [
        {"id": "a", "kind": "tool", "name": "a", "description": "d"},
        {
            "id": "b",
            "kind": "tool",
            "name": "b",
            "description": "d",
            "depends_on": ["ghost"],
        },
    ]
    with caplog.at_level("WARNING", logger="contextweaver.routing"):
        items = load_catalog_dicts(data)
    assert len(items) == 2  # warn never drops items
    assert any("ghost" in r.message for r in caplog.records)


def test_load_catalog_dicts_raise_mode_attaches_report() -> None:
    data = [
        {
            "id": "b",
            "kind": "tool",
            "name": "b",
            "description": "d",
            "depends_on": ["ghost"],
        },
    ]
    with pytest.raises(CatalogValidationError) as excinfo:
        load_catalog_dicts(data, on_invalid="raise")
    assert excinfo.value.report.findings[0].missing == "ghost"


def test_load_catalog_dicts_ignore_mode_skips_validation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    data = [
        {
            "id": "b",
            "kind": "tool",
            "name": "b",
            "description": "d",
            "depends_on": ["ghost"],
        },
    ]
    with caplog.at_level("WARNING", logger="contextweaver.routing"):
        items = load_catalog_dicts(data, on_invalid="ignore")
    assert len(items) == 1
    assert not any("ghost" in r.message for r in caplog.records)


def test_load_catalog_dicts_rejects_unknown_on_invalid_policy() -> None:
    data = [{"id": "a", "kind": "tool", "name": "a", "description": "d"}]
    with pytest.raises(CatalogError, match="invalid on_invalid policy"):
        load_catalog_dicts(data, on_invalid="wirn")  # type: ignore[arg-type]


def test_load_catalog_dicts_invalid_item_names_id() -> None:
    data = [{"id": "good", "kind": "tool", "name": "good", "description": "d"}, {"id": "oops"}]
    with pytest.raises(CatalogError, match="'oops'"):
        load_catalog_dicts(data)


def test_load_catalog_dicts_invalid_item_without_id_uses_index() -> None:
    data = [{"kind": "tool", "name": "x", "description": "d"}]
    with pytest.raises(CatalogError, match="at index 0"):
        load_catalog_dicts(data)


# ------------------------------------------------------------------
# load_catalog_json
# ------------------------------------------------------------------


def test_load_catalog_json() -> None:
    data = [
        {"id": "t1", "kind": "tool", "name": "t1", "description": "desc"},
        {"id": "t2", "kind": "tool", "name": "t2", "description": "desc"},
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
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
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        f.write("{not valid json")
        path = f.name
    try:
        with pytest.raises(CatalogError, match="Invalid JSON"):
            load_catalog_json(path)
    finally:
        Path(path).unlink()


def test_load_catalog_yaml() -> None:
    yaml_text = (
        "- id: t1\n  kind: tool\n  name: t1\n  description: desc\n"
        "- id: t2\n  kind: tool\n  name: t2\n  description: desc\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(yaml_text)
        path = f.name
    try:
        items = load_catalog_yaml(path)
        assert len(items) == 2
        assert items[0].id == "t1"
        assert items[1].id == "t2"
    finally:
        Path(path).unlink()


def test_load_catalog_yaml_invalid() -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write("- id: t1\n  : invalid\n  bad indent\n")
        path = f.name
    try:
        with pytest.raises(CatalogError, match="Invalid YAML"):
            load_catalog_yaml(path)
    finally:
        Path(path).unlink()


def test_load_catalog_yaml_not_sequence() -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write("items: []\n")
        path = f.name
    try:
        with pytest.raises(CatalogError, match="must be a sequence"):
            load_catalog_yaml(path)
    finally:
        Path(path).unlink()


def test_load_catalog_auto_detects_yaml() -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False, encoding="utf-8") as f:
        f.write("- id: t1\n  kind: tool\n  name: t1\n  description: desc\n")
        path = f.name
    try:
        items = load_catalog(path)
        assert len(items) == 1
        assert items[0].id == "t1"
    finally:
        Path(path).unlink()


def test_load_catalog_auto_detects_json() -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump([{"id": "t1", "kind": "tool", "name": "t1", "description": "desc"}], f)
        path = f.name
    try:
        items = load_catalog(path)
        assert items[0].id == "t1"
    finally:
        Path(path).unlink()


def test_load_catalog_json_not_array() -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
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


# ------------------------------------------------------------------
# Catalog.hydrate
# ------------------------------------------------------------------


def test_hydrate_returns_hydration_result() -> None:
    catalog = Catalog()
    catalog.register(
        SelectableItem(
            id="t1",
            kind="tool",
            name="search_db",
            description="Search the database",
            args_schema={"q": {"type": "string"}},
            examples=["search_db(q='users')"],
            constraints={"max_results": 100},
        )
    )
    result = catalog.hydrate("t1")
    assert isinstance(result, HydrationResult)
    assert result.item.id == "t1"
    assert result.args_schema == {"q": {"type": "string"}}
    assert result.examples == ["search_db(q='users')"]
    assert result.constraints == {"max_results": 100}


def test_hydrate_missing_raises() -> None:
    catalog = Catalog()
    with pytest.raises(ItemNotFoundError):
        catalog.hydrate("nonexistent")


def test_hydrate_empty_schema() -> None:
    catalog = Catalog()
    catalog.register(_item("t1"))
    result = catalog.hydrate("t1")
    assert result.args_schema == {}
    assert result.examples == []
    assert result.constraints == {}


def test_hydrate_roundtrip() -> None:
    catalog = Catalog()
    catalog.register(
        SelectableItem(
            id="t1",
            kind="tool",
            name="send_email",
            description="Send an email",
            args_schema={"to": {"type": "string"}, "body": {"type": "string"}},
            examples=["send_email(to='a@b.com', body='hi')"],
            constraints={"rate_limit": "10/min"},
        )
    )
    result = catalog.hydrate("t1")
    d = result.to_dict()
    restored = HydrationResult.from_dict(d)
    assert restored.item.id == "t1"
    assert restored.args_schema == result.args_schema
    assert restored.examples == result.examples
    assert restored.constraints == result.constraints

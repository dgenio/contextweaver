"""Tests for contextweaver.routing.hydration (issue #261)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contextweaver.exceptions import CatalogError, ItemNotFoundError
from contextweaver.routing.catalog import Catalog
from contextweaver.routing.hydration import (
    SchemaSource,
    hydrate_with_schema,
    lazy_schema_resolver,
)
from contextweaver.types import SelectableItem


def _item(iid: str, *, args_schema: dict | None = None) -> SelectableItem:
    return SelectableItem(
        id=iid,
        kind="tool",
        name=iid.replace(".", "_"),
        description=f"desc for {iid}",
        namespace=iid.split(".")[0] if "." in iid else "default",
        tags=[],
        args_schema=args_schema or {},
    )


def _catalog(items: list[SelectableItem]) -> Catalog:
    cat = Catalog()
    for item in items:
        cat.register(item)
    return cat


# ---------------------------------------------------------------------------
# SchemaSource — direct construction
# ---------------------------------------------------------------------------


def test_schema_source_returns_registered_schema() -> None:
    source = SchemaSource({"bigquery.run_query": {"type": "object"}})
    assert source.get_schema("bigquery.run_query") == {"type": "object"}


def test_schema_source_returns_none_for_missing_id() -> None:
    source = SchemaSource({"a.b": {"type": "object"}})
    assert source.get_schema("missing.tool") is None


def test_schema_source_returns_shallow_copy_so_caller_mutation_does_not_leak() -> None:
    source = SchemaSource({"x.y": {"type": "object", "required": ["a"]}})
    schema = source.get_schema("x.y")
    assert schema is not None
    schema["required"] = ["mutated"]
    assert source.get_schema("x.y") == {"type": "object", "required": ["a"]}


def test_schema_source_constructor_snapshots_input_dict() -> None:
    original: dict[str, dict] = {"a.b": {"type": "object"}}
    source = SchemaSource(original)
    original["a.b"]["mutated"] = True
    original["c.d"] = {"type": "string"}
    assert source.get_schema("a.b") == {"type": "object"}
    assert source.get_schema("c.d") is None


def test_schema_source_empty_constructor_yields_no_schemas() -> None:
    source = SchemaSource()
    assert source.get_schema("anything") is None
    assert source.known_ids() == []


def test_schema_source_known_ids_returns_sorted_keys() -> None:
    source = SchemaSource({"z.last": {}, "a.first": {}, "m.middle": {}})
    assert source.known_ids() == ["a.first", "m.middle", "z.last"]


# ---------------------------------------------------------------------------
# SchemaSource — from_json_file
# ---------------------------------------------------------------------------


def test_from_json_file_accepts_flat_mapping(tmp_path: Path) -> None:
    payload = {"bigquery.run_query": {"type": "object", "required": ["sql"]}}
    path = tmp_path / "schemas.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    source = SchemaSource.from_json_file(path)
    assert source.get_schema("bigquery.run_query") == {
        "type": "object",
        "required": ["sql"],
    }


def test_from_json_file_accepts_mcp_tools_envelope(tmp_path: Path) -> None:
    payload = {
        "tools": [
            {
                "name": "github.create_issue",
                "description": "Open a GitHub issue.",
                "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}}},
            },
            {
                "name": "linear.create_ticket",
                "inputSchema": {"type": "object"},
            },
        ]
    }
    path = tmp_path / "tools.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    source = SchemaSource.from_json_file(path)
    assert source.get_schema("github.create_issue") == {
        "type": "object",
        "properties": {"title": {"type": "string"}},
    }
    assert source.get_schema("linear.create_ticket") == {"type": "object"}


def test_from_json_file_missing_file_raises_catalog_error(tmp_path: Path) -> None:
    with pytest.raises(CatalogError, match="Cannot read schema-source file"):
        SchemaSource.from_json_file(tmp_path / "does_not_exist.json")


def test_from_json_file_invalid_json_raises_catalog_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{this is not json}", encoding="utf-8")
    with pytest.raises(CatalogError, match="Invalid JSON"):
        SchemaSource.from_json_file(path)


def test_from_json_file_non_object_top_level_raises(tmp_path: Path) -> None:
    path = tmp_path / "arr.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(CatalogError, match="must be a JSON object"):
        SchemaSource.from_json_file(path)


# ---------------------------------------------------------------------------
# SchemaSource — from_mcp_tools
# ---------------------------------------------------------------------------


def test_from_mcp_tools_with_schema_indexes_by_name() -> None:
    defs = [
        {
            "name": "bigquery.run_query",
            "description": "Run a SQL query.",
            "inputSchema": {"type": "object", "required": ["sql"]},
        }
    ]
    source = SchemaSource.from_mcp_tools(defs)
    assert source.get_schema("bigquery.run_query") == {
        "type": "object",
        "required": ["sql"],
    }


def test_from_mcp_tools_silently_skips_defs_with_no_schema() -> None:
    defs = [
        {"name": "schemaless.tool", "description": "no schema attached"},
        {
            "name": "scoped.tool",
            "inputSchema": {"type": "object"},
        },
    ]
    source = SchemaSource.from_mcp_tools(defs)
    assert source.get_schema("schemaless.tool") is None
    assert source.get_schema("scoped.tool") == {"type": "object"}


def test_from_mcp_tools_rejects_non_mapping() -> None:
    with pytest.raises(CatalogError, match="must be a mapping"):
        SchemaSource.from_mcp_tools(["not a dict"])  # type: ignore[list-item]


def test_from_mcp_tools_rejects_def_without_name() -> None:
    with pytest.raises(CatalogError, match="missing non-empty 'name'"):
        SchemaSource.from_mcp_tools([{"inputSchema": {"type": "object"}}])


def test_from_mcp_tools_rejects_empty_name() -> None:
    with pytest.raises(CatalogError, match="missing non-empty 'name'"):
        SchemaSource.from_mcp_tools([{"name": "", "inputSchema": {"type": "object"}}])


# ---------------------------------------------------------------------------
# hydrate_with_schema
# ---------------------------------------------------------------------------


def test_hydrate_with_schema_uses_catalog_schema_when_inline_populated() -> None:
    catalog = _catalog([_item("a.b", args_schema={"type": "string", "from": "inline"})])
    source = SchemaSource({"a.b": {"type": "object", "from": "sidecar"}})
    result = hydrate_with_schema(catalog, "a.b", source)
    # Inline schema wins — sidecar must not override.
    assert result.args_schema == {"type": "string", "from": "inline"}


def test_hydrate_with_schema_merges_sidecar_when_inline_empty() -> None:
    catalog = _catalog([_item("a.b")])
    source = SchemaSource({"a.b": {"type": "object", "required": ["x"]}})
    result = hydrate_with_schema(catalog, "a.b", source)
    assert result.args_schema == {"type": "object", "required": ["x"]}
    # Catalog metadata still flows through.
    assert result.item.id == "a.b"


def test_hydrate_with_schema_no_source_returns_catalog_result_unchanged() -> None:
    catalog = _catalog([_item("a.b")])
    result = hydrate_with_schema(catalog, "a.b")
    assert result.args_schema == {}


def test_hydrate_with_schema_accepts_plain_mapping_as_source() -> None:
    catalog = _catalog([_item("a.b")])
    plain = {"a.b": {"type": "object"}}
    result = hydrate_with_schema(catalog, "a.b", plain)
    assert result.args_schema == {"type": "object"}


def test_hydrate_with_schema_missing_id_raises_item_not_found() -> None:
    catalog = _catalog([_item("a.b")])
    source = SchemaSource({"missing.tool": {"type": "object"}})
    with pytest.raises(ItemNotFoundError):
        hydrate_with_schema(catalog, "missing.tool", source)


def test_hydrate_with_schema_sidecar_missing_for_id_returns_empty_schema() -> None:
    catalog = _catalog([_item("a.b")])
    source = SchemaSource({"c.d": {"type": "object"}})
    result = hydrate_with_schema(catalog, "a.b", source)
    assert result.args_schema == {}


def test_hydrate_with_schema_returns_fresh_dict_caller_mutation_safe() -> None:
    catalog = _catalog([_item("a.b")])
    source = SchemaSource({"a.b": {"type": "object"}})
    first = hydrate_with_schema(catalog, "a.b", source)
    first.args_schema["mutated"] = True
    second = hydrate_with_schema(catalog, "a.b", source)
    assert "mutated" not in second.args_schema


# ---------------------------------------------------------------------------
# lazy_schema_resolver
# ---------------------------------------------------------------------------


def test_lazy_schema_resolver_callable_returns_schema_dict() -> None:
    catalog = _catalog([_item("a.b")])
    resolver = lazy_schema_resolver(catalog, {"a.b": {"type": "object"}})
    schema = resolver("a.b")
    assert schema == {"type": "object"}


def test_lazy_schema_resolver_callable_returns_none_for_missing_schema() -> None:
    catalog = _catalog([_item("a.b")])
    resolver = lazy_schema_resolver(catalog)
    assert resolver("a.b") is None


def test_lazy_schema_resolver_callable_returns_none_for_missing_id() -> None:
    catalog = _catalog([_item("a.b")])
    resolver = lazy_schema_resolver(catalog, {"missing.tool": {"type": "object"}})
    # Missing id must NOT raise from the callable form — safe in template contexts.
    assert resolver("missing.tool") is None


def test_lazy_schema_resolver_hydrate_returns_full_result() -> None:
    catalog = _catalog([_item("a.b")])
    resolver = lazy_schema_resolver(catalog, {"a.b": {"type": "object"}})
    result = resolver.hydrate("a.b")
    assert result.item.id == "a.b"
    assert result.args_schema == {"type": "object"}


def test_lazy_schema_resolver_hydrate_raises_for_missing_id() -> None:
    catalog = _catalog([_item("a.b")])
    resolver = lazy_schema_resolver(catalog)
    with pytest.raises(ItemNotFoundError):
        resolver.hydrate("missing.tool")


def test_lazy_schema_resolver_accepts_schema_source_directly() -> None:
    catalog = _catalog([_item("a.b")])
    source = SchemaSource({"a.b": {"type": "object", "marker": "from-source-instance"}})
    resolver = lazy_schema_resolver(catalog, source)
    assert resolver("a.b") == {"type": "object", "marker": "from-source-instance"}

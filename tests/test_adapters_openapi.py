"""Tests for contextweaver.adapters.openapi (issue #546)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from contextweaver.adapters.openapi import (
    infer_openapi_namespace,
    load_openapi_catalog,
    openapi_operation_to_selectable,
    openapi_spec_to_catalog,
)
from contextweaver.exceptions import CatalogError
from contextweaver.routing.cards import cards_for_route
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder

PETSTORE: dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {"title": "Petstore", "version": "1.0.0"},
    "servers": [{"url": "https://api.example.com"}],
    "components": {
        "schemas": {
            "Pet": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "tag": {"type": "string"}},
                "required": ["name"],
            }
        },
        "parameters": {
            "PetId": {
                "name": "petId",
                "in": "path",
                "required": True,
                "description": "id of the pet",
                "schema": {"type": "integer"},
            }
        },
    },
    "paths": {
        "/pets": {
            "get": {
                "operationId": "listPets",
                "summary": "List all pets",
                "tags": ["pets"],
                "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer"}}],
            },
            "post": {
                "operationId": "createPet",
                "summary": "Create a pet",
                "tags": ["pets"],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Pet"}}
                    },
                },
            },
        },
        "/pets/{petId}": {
            "get": {
                "operationId": "getPet",
                "summary": "Get a pet",
                "tags": ["pets"],
                "parameters": [{"$ref": "#/components/parameters/PetId"}],
            },
            "delete": {
                "operationId": "deletePet",
                "summary": "Delete a pet",
                "tags": ["pets"],
                "parameters": [{"$ref": "#/components/parameters/PetId"}],
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Operation → SelectableItem
# ---------------------------------------------------------------------------


def test_operation_get_read_only_no_side_effects() -> None:
    op = PETSTORE["paths"]["/pets"]["get"]
    item = openapi_operation_to_selectable(op, path="/pets", method="get", root=PETSTORE)
    assert item.id == "openapi:listPets"
    assert item.kind == "tool"
    assert item.side_effects is False
    assert "read-only" in item.tags
    assert item.namespace == "pets"
    assert item.metadata["http_method"] == "GET"
    assert item.metadata["http_path"] == "/pets"
    assert item.args_schema["properties"]["limit"] == {"type": "integer"}


def test_operation_delete_is_destructive_with_side_effects() -> None:
    op = PETSTORE["paths"]["/pets/{petId}"]["delete"]
    item = openapi_operation_to_selectable(op, path="/pets/{petId}", method="delete", root=PETSTORE)
    assert item.side_effects is True
    assert "destructive" in item.tags


def test_operation_resolves_parameter_ref_into_args_schema() -> None:
    op = PETSTORE["paths"]["/pets/{petId}"]["get"]
    item = openapi_operation_to_selectable(op, path="/pets/{petId}", method="get", root=PETSTORE)
    props = item.args_schema["properties"]
    assert props["petId"]["type"] == "integer"
    assert props["petId"]["description"] == "id of the pet"
    assert item.args_schema["required"] == ["petId"]


def test_operation_request_body_object_merged() -> None:
    op = PETSTORE["paths"]["/pets"]["post"]
    item = openapi_operation_to_selectable(op, path="/pets", method="post", root=PETSTORE)
    props = item.args_schema["properties"]
    assert props["name"] == {"type": "string"}
    assert props["tag"] == {"type": "string"}
    assert item.args_schema["required"] == ["name"]
    assert item.side_effects is True


def test_operation_request_body_nested_on_collision() -> None:
    spec = {
        "paths": {
            "/x": {
                "post": {
                    "operationId": "collide",
                    "parameters": [{"name": "name", "in": "query", "schema": {"type": "string"}}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "integer"}},
                                }
                            }
                        },
                    },
                }
            }
        }
    }
    op = spec["paths"]["/x"]["post"]
    item = openapi_operation_to_selectable(op, path="/x", method="post", root=spec)
    # The query param keeps the top-level "name"; the colliding body lands under "body".
    assert item.args_schema["properties"]["name"] == {"type": "string"}
    assert "body" in item.args_schema["properties"]
    assert "body" in item.args_schema["required"]


def test_operation_non_dict_parameter_schema_coerced_to_empty() -> None:
    # A malformed spec where ``schema`` is a string must not raise a bare
    # TypeError/ValueError — it coerces to {} so invalid specs fail (or pass)
    # deterministically through the typed CatalogError contract.
    op = {
        "operationId": "x",
        "parameters": [{"name": "q", "in": "query", "schema": "not-a-mapping"}],
    }
    item = openapi_operation_to_selectable(op, path="/x", method="get", root={})
    assert item.args_schema["properties"]["q"] == {}


def test_operation_non_list_tags_ignored() -> None:
    # A string ``tags`` must not be exploded into per-character tags.
    op = {"operationId": "x", "tags": "pets"}
    item = openapi_operation_to_selectable(op, path="/x", method="get", root={})
    assert item.tags == ["openapi", "read-only"]


def test_operation_id_fallback_is_deterministic() -> None:
    op = {"summary": "no id here"}
    item = openapi_operation_to_selectable(op, path="/foo/{bar}/baz", method="get", root={})
    assert item.id == "openapi:get_foo_bar_baz"
    assert item.name == "get_foo_bar_baz"


def test_operation_description_falls_back_to_method_path() -> None:
    item = openapi_operation_to_selectable(
        {"operationId": "x"}, path="/health", method="get", root={}
    )
    assert item.description == "GET /health"


def test_external_ref_raises() -> None:
    op = {
        "operationId": "x",
        "parameters": [{"$ref": "https://example.com/external.json#/p"}],
    }
    with pytest.raises(CatalogError, match="external"):
        openapi_operation_to_selectable(op, path="/x", method="get", root={})


# ---------------------------------------------------------------------------
# Namespace inference
# ---------------------------------------------------------------------------


def test_infer_namespace_prefers_base() -> None:
    assert infer_openapi_namespace({"tags": ["pets"]}, "/pets", base_namespace="api") == "api"


def test_infer_namespace_uses_first_tag() -> None:
    assert infer_openapi_namespace({"tags": ["pets"]}, "/x/y") == "pets"


def test_infer_namespace_falls_back_to_path_segment() -> None:
    assert infer_openapi_namespace({}, "/store/orders") == "store"


def test_infer_namespace_default() -> None:
    assert infer_openapi_namespace({}, "/{id}") == "openapi"


# ---------------------------------------------------------------------------
# Spec → Catalog
# ---------------------------------------------------------------------------


def test_spec_to_catalog_registers_every_operation() -> None:
    catalog = openapi_spec_to_catalog(PETSTORE)
    ids = {item.id for item in catalog.all()}
    assert ids == {
        "openapi:listPets",
        "openapi:createPet",
        "openapi:getPet",
        "openapi:deletePet",
    }


def test_spec_to_catalog_base_namespace_applied() -> None:
    catalog = openapi_spec_to_catalog(PETSTORE, base_namespace="petstore")
    assert {item.namespace for item in catalog.all()} == {"petstore"}


def test_spec_missing_paths_raises() -> None:
    with pytest.raises(CatalogError, match="'paths'"):
        openapi_spec_to_catalog({"openapi": "3.0.0"})


def test_duplicate_operation_id_raises() -> None:
    spec = {
        "paths": {
            "/a": {"get": {"operationId": "dup", "summary": "a"}},
            "/b": {"get": {"operationId": "dup", "summary": "b"}},
        }
    }
    with pytest.raises(CatalogError, match="Duplicate item id"):
        openapi_spec_to_catalog(spec)


# ---------------------------------------------------------------------------
# File loading (JSON + YAML) and routing
# ---------------------------------------------------------------------------


def test_load_openapi_catalog_from_json(tmp_path: Path) -> None:
    path = tmp_path / "petstore.json"
    path.write_text(json.dumps(PETSTORE), encoding="utf-8")
    catalog = load_openapi_catalog(path)
    assert len(catalog.all()) == 4


def test_load_openapi_catalog_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "petstore.yaml"
    path.write_text(yaml.safe_dump(PETSTORE), encoding="utf-8")
    catalog = load_openapi_catalog(path)
    assert len(catalog.all()) == 4


def test_load_openapi_catalog_bad_path_raises() -> None:
    with pytest.raises(CatalogError, match="Cannot read OpenAPI spec"):
        load_openapi_catalog("/no/such/spec.json")


def test_openapi_routes_to_bounded_cards() -> None:
    catalog = openapi_spec_to_catalog(PETSTORE)
    items = catalog.all()
    router = Router(TreeBuilder().build(items), items=items, beam_width=3)
    result = router.route("remove a pet from the store")
    cards = cards_for_route(result.candidate_ids, catalog)
    assert cards
    assert "openapi:deletePet" in result.candidate_ids


def test_large_spec_performance_smoke() -> None:
    paths = {
        f"/resource{i}": {"get": {"operationId": f"getResource{i}", "summary": f"Get resource {i}"}}
        for i in range(300)
    }
    catalog = openapi_spec_to_catalog({"paths": paths})
    assert len(catalog.all()) == 300

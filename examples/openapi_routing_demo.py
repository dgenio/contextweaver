"""OpenAPI → catalog routing demo (issue #546).

Demonstrates turning an OpenAPI document into a routing
:class:`~contextweaver.routing.catalog.Catalog` — every operation becomes a
:class:`~contextweaver.types.SelectableItem` — so a natural-language request is
narrowed to a bounded set of REST operations instead of dumping the whole spec
into the prompt.

contextweaver *routes*; it never makes the HTTP call.  The selected operation's
``metadata`` carries the method + path so the caller's own HTTP client can
dispatch it.  Runs offline against an inline spec dict (no network).
"""

from __future__ import annotations

from typing import Any

from contextweaver.adapters.openapi import openapi_spec_to_catalog
from contextweaver.routing.cards import cards_for_route, format_card_for_prompt
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder

SPEC: dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {"title": "Store API", "version": "1.0.0"},
    "paths": {
        "/pets": {
            "get": {
                "operationId": "listPets",
                "summary": "List all pets in the store",
                "tags": ["pets"],
                "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer"}}],
            },
            "post": {
                "operationId": "createPet",
                "summary": "Add a new pet to the store",
                "tags": ["pets"],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"name": {"type": "string"}},
                                "required": ["name"],
                            }
                        }
                    },
                },
            },
        },
        "/pets/{petId}": {
            "delete": {
                "operationId": "deletePet",
                "summary": "Delete a pet from the store",
                "tags": ["pets"],
                "parameters": [
                    {"name": "petId", "in": "path", "required": True, "schema": {"type": "integer"}}
                ],
            }
        },
        "/orders": {
            "get": {
                "operationId": "listOrders",
                "summary": "List purchase orders",
                "tags": ["orders"],
            }
        },
    },
}


def main() -> None:
    catalog = openapi_spec_to_catalog(SPEC)
    items = catalog.all()
    router = Router(TreeBuilder().build(items), items=items, beam_width=3)

    query = "remove a pet from the catalog"
    print(f"Loaded {len(items)} REST operations from the OpenAPI spec.")
    print(f"Query: {query!r}\n")

    result = router.route(query)
    cards = cards_for_route(result.candidate_ids, catalog)
    print("Bounded operation shortlist:")
    for card in cards:
        print(format_card_for_prompt(card))

    top = catalog.get(result.candidate_ids[0])
    print(f"\nSelected operation: {top.id}")
    print(f"  Dispatch with: {top.metadata['http_method']} {top.metadata['http_path']}")
    print(f"  Side effects: {top.side_effects}; tags: {top.tags}")


if __name__ == "__main__":
    main()

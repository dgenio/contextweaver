"""Tests for contextweaver.adapters.chainweaver (issue #334)."""

from __future__ import annotations

import pytest

from contextweaver.adapters.chainweaver import (
    FLOW_TAG,
    chainweaver_flow_to_selectable,
    chainweaver_flows_to_catalog,
    load_chainweaver_export,
)
from contextweaver.exceptions import CatalogError
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import SelectableItem


def _flow(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "customer_summary_flow",
        "name": "Summarize customer history",
        "description": "Fetch and summarise a customer's recent activity.",
        "version": "1.2.0",
        "input_schema": {"type": "object", "properties": {"customer_id": {"type": "string"}}},
        "output_schema": {"type": "object", "properties": {"summary": {"type": "string"}}},
        "tags": ["customer", "summary"],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Single-flow conversion
# ---------------------------------------------------------------------------


def test_flow_to_selectable_preserves_core_fields() -> None:
    item = chainweaver_flow_to_selectable(_flow())
    assert isinstance(item, SelectableItem)
    assert item.id == "chainweaver:customer_summary_flow"
    assert item.kind == "flow"
    assert item.name == "Summarize customer history"
    assert item.description.startswith("Fetch and summarise")
    assert item.namespace == "chainweaver"


def test_flow_to_selectable_preserves_schemas() -> None:
    item = chainweaver_flow_to_selectable(_flow())
    assert item.args_schema["properties"]["customer_id"]["type"] == "string"
    assert item.output_schema is not None
    assert item.output_schema["properties"]["summary"]["type"] == "string"


def test_flow_to_selectable_stamps_metadata() -> None:
    item = chainweaver_flow_to_selectable(_flow())
    assert item.metadata["runtime"] == "chainweaver"
    assert item.metadata["chainweaver_flow_id"] == "customer_summary_flow"
    assert item.metadata["chainweaver_flow_version"] == "1.2.0"


def test_flow_to_selectable_always_tagged_flow() -> None:
    item = chainweaver_flow_to_selectable(_flow(tags=[]))
    assert FLOW_TAG in item.tags


def test_flow_to_selectable_does_not_mutate_export() -> None:
    export = _flow()
    chainweaver_flow_to_selectable(export)
    # args_schema is deep-copied, so mutating the item must not touch the export.
    item = chainweaver_flow_to_selectable(export)
    item.args_schema["properties"]["injected"] = {"type": "string"}
    assert "injected" not in export["input_schema"]  # type: ignore[index]


def test_flow_id_alias_and_inputs_outputs_aliases() -> None:
    item = chainweaver_flow_to_selectable(
        {
            "flow_id": "f1",
            "name": "Flow One",
            "description": "Alias-keyed flow.",
            "inputs": {"type": "object"},
            "outputs": {"type": "object"},
        }
    )
    assert item.id == "chainweaver:f1"
    assert item.args_schema == {"type": "object"}
    assert item.output_schema == {"type": "object"}


def test_namespace_override() -> None:
    item = chainweaver_flow_to_selectable(_flow(), namespace="billing")
    assert item.namespace == "billing"
    # The id keeps the canonical chainweaver: prefix regardless of namespace.
    assert item.id == "chainweaver:customer_summary_flow"


def test_missing_output_schema_is_none() -> None:
    item = chainweaver_flow_to_selectable(
        {"id": "f", "name": "F", "description": "No output schema."}
    )
    assert item.output_schema is None
    assert item.args_schema == {}


@pytest.mark.parametrize(
    "bad",
    [
        {"name": "x", "description": "y"},  # missing id
        {"id": "f", "description": "y"},  # missing name
        {"id": "f", "name": "x"},  # missing description
        {"id": "", "name": "x", "description": "y"},  # empty id
    ],
)
def test_invalid_flow_raises(bad: dict[str, object]) -> None:
    with pytest.raises(CatalogError):
        chainweaver_flow_to_selectable(bad)


def test_non_dict_flow_raises_catalog_error() -> None:
    # A malformed export element must raise CatalogError, not AttributeError.
    with pytest.raises(CatalogError):
        chainweaver_flow_to_selectable("not a dict")  # type: ignore[arg-type]


def test_flows_to_catalog_with_non_dict_element_raises_catalog_error() -> None:
    with pytest.raises(CatalogError):
        chainweaver_flows_to_catalog([_flow(), "oops"])  # type: ignore[list-item]


# ---------------------------------------------------------------------------
# Catalog / export loading
# ---------------------------------------------------------------------------


def test_flows_to_catalog_registers_all() -> None:
    catalog = chainweaver_flows_to_catalog(
        [_flow(), _flow(id="refund_flow", name="Refund", description="Issue a refund.")]
    )
    ids = {it.id for it in catalog.all()}
    assert ids == {"chainweaver:customer_summary_flow", "chainweaver:refund_flow"}


def test_load_export_accepts_list() -> None:
    catalog = load_chainweaver_export([_flow()])
    assert len(catalog.all()) == 1


def test_load_export_accepts_flows_dict() -> None:
    catalog = load_chainweaver_export({"flows": [_flow()]})
    assert len(catalog.all()) == 1


def test_load_export_rejects_dict_without_flows() -> None:
    with pytest.raises(CatalogError):
        load_chainweaver_export({"not_flows": []})


def test_load_export_rejects_bad_type() -> None:
    with pytest.raises(CatalogError):
        load_chainweaver_export("nope")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Routing integration: a flow is routable and renders to a flow card
# ---------------------------------------------------------------------------


def test_imported_flow_is_routable_and_renders_flow_card() -> None:
    items = [
        SelectableItem(
            id="t1", kind="tool", name="lookup", description="Look up a customer record"
        ),
        chainweaver_flow_to_selectable(_flow()),
    ]
    graph = TreeBuilder(max_children=20).build(items)
    router = Router(graph, items=items, top_k=20)
    result = router.route("summarize customer history")
    assert "chainweaver:customer_summary_flow" in result.candidate_ids
    decision = result.to_routing_decision()
    flow_cards = [c for c in decision.choice_cards if c.kind == "flow"]
    assert any(c.id == "chainweaver:customer_summary_flow" for c in flow_cards)

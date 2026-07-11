"""Tests for contextweaver.routing.catalog_metadata (issue #377).

Coverage spans full / partial / missing inventory payloads, strict validation
(bad enum values, unknown keys, non-string fields), the attach/read helpers'
no-shared-mutation contract, the reserved ``metadata["_contextweaver"]``
namespace rules, lossless round-trips through ``SelectableItem`` serde, and
the deprecated-lifecycle helper.
"""

from __future__ import annotations

import pytest

from contextweaver.exceptions import CatalogError, ConfigError
from contextweaver.routing.catalog_metadata import (
    CW_METADATA_KEY,
    INVENTORY_KEY,
    InventoryMetadata,
    attach_inventory,
    inventory_of,
    is_deprecated,
    validate_inventory,
)
from contextweaver.types import SelectableItem

FULL = InventoryMetadata(
    owner_team="payments",
    business_domain="billing",
    contact="#payments-oncall",
    source_repo="acme/billing-tools",
    risk_level="high",
    side_effects="destructive",
    lifecycle="active",
    environment="prod",
    service_tier="critical",
)


def _item(**overrides: object) -> SelectableItem:
    base: dict[str, object] = {
        "id": "billing.refund",
        "kind": "tool",
        "name": "refund",
        "description": "Refund a payment.",
    }
    base.update(overrides)
    return SelectableItem(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# InventoryMetadata serde and validation
# ---------------------------------------------------------------------------


def test_full_metadata_round_trips_through_to_dict_from_dict() -> None:
    assert InventoryMetadata.from_dict(FULL.to_dict()) == FULL


def test_partial_metadata_omits_none_fields_from_payload() -> None:
    inv = InventoryMetadata(owner_team="payments", lifecycle="deprecated")
    assert inv.to_dict() == {"owner_team": "payments", "lifecycle": "deprecated"}
    assert InventoryMetadata.from_dict(inv.to_dict()) == inv


def test_empty_metadata_serialises_to_empty_payload() -> None:
    assert InventoryMetadata().to_dict() == {}
    assert InventoryMetadata.from_dict({}) == InventoryMetadata()


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("risk_level", "extreme"),
        ("side_effects", "mutating"),
        ("lifecycle", "retired"),
        ("environment", "production"),
        ("service_tier", "gold"),
    ],
)
def test_bad_enum_value_raises_config_error_with_hint(field_name: str, value: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        validate_inventory({field_name: value})
    assert field_name in str(excinfo.value)
    assert excinfo.value.hint is not None and "allowed values" in excinfo.value.hint


def test_direct_construction_with_bad_enum_raises_config_error() -> None:
    with pytest.raises(ConfigError):
        InventoryMetadata(risk_level="extreme")  # type: ignore[arg-type]


def test_unknown_key_raises_config_error_with_allowed_fields_hint() -> None:
    with pytest.raises(ConfigError) as excinfo:
        validate_inventory({"owner": "payments"})
    assert "owner" in str(excinfo.value)
    assert excinfo.value.hint is not None and "owner_team" in excinfo.value.hint


def test_non_string_value_for_string_field_raises_config_error() -> None:
    with pytest.raises(ConfigError):
        validate_inventory({"owner_team": 42})


def test_non_mapping_payload_raises_config_error() -> None:
    with pytest.raises(ConfigError):
        validate_inventory(["owner_team"])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# attach_inventory / inventory_of
# ---------------------------------------------------------------------------


def test_attach_and_read_back() -> None:
    annotated = attach_inventory(_item(), FULL)
    assert inventory_of(annotated) == FULL
    assert annotated.metadata[CW_METADATA_KEY][INVENTORY_KEY] == FULL.to_dict()


def test_attach_does_not_mutate_the_original_item_or_shared_dicts() -> None:
    shared_namespace = {"provenance": "unit-test"}
    item = _item(metadata={"team": "core", CW_METADATA_KEY: shared_namespace})
    annotated = attach_inventory(item, InventoryMetadata(owner_team="payments"))
    assert annotated is not item
    assert INVENTORY_KEY not in shared_namespace  # shared dict untouched
    assert item.metadata[CW_METADATA_KEY] == {"provenance": "unit-test"}
    assert inventory_of(item) is None
    # Sibling keys survive on the new item, inside and outside the namespace.
    assert annotated.metadata["team"] == "core"
    assert annotated.metadata[CW_METADATA_KEY]["provenance"] == "unit-test"


def test_attach_replaces_an_existing_inventory_payload() -> None:
    annotated = attach_inventory(_item(), InventoryMetadata(lifecycle="experimental"))
    annotated = attach_inventory(annotated, InventoryMetadata(lifecycle="active"))
    inv = inventory_of(annotated)
    assert inv is not None and inv.lifecycle == "active"
    assert inv.owner_team is None


def test_attach_rejects_a_non_mapping_reserved_namespace() -> None:
    item = _item(metadata={CW_METADATA_KEY: "not-a-dict"})
    with pytest.raises(CatalogError):
        attach_inventory(item, InventoryMetadata())


def test_inventory_of_missing_metadata_returns_none() -> None:
    assert inventory_of(_item()) is None
    assert inventory_of(_item(metadata={CW_METADATA_KEY: {}})) is None
    assert inventory_of(_item(metadata={CW_METADATA_KEY: "not-a-dict"})) is None


def test_inventory_of_corrupt_payload_raises_config_error() -> None:
    with pytest.raises(ConfigError):
        inventory_of(_item(metadata={CW_METADATA_KEY: {INVENTORY_KEY: "corrupt"}}))
    with pytest.raises(ConfigError):
        inventory_of(_item(metadata={CW_METADATA_KEY: {INVENTORY_KEY: {"risk_level": "x"}}}))


def test_attach_never_touches_the_boolean_side_effects_routing_field() -> None:
    item = _item(side_effects=False)
    annotated = attach_inventory(item, InventoryMetadata(side_effects="destructive"))
    assert annotated.side_effects is False


# ---------------------------------------------------------------------------
# SelectableItem serde round-trip
# ---------------------------------------------------------------------------


def test_inventory_round_trips_through_selectable_item_serde() -> None:
    annotated = attach_inventory(_item(), FULL)
    restored = SelectableItem.from_dict(annotated.to_dict())
    assert restored == annotated
    assert inventory_of(restored) == FULL


def test_partial_inventory_round_trips_through_selectable_item_serde() -> None:
    inv = InventoryMetadata(owner_team="payments", environment="staging")
    restored = SelectableItem.from_dict(attach_inventory(_item(), inv).to_dict())
    assert inventory_of(restored) == inv


# ---------------------------------------------------------------------------
# is_deprecated
# ---------------------------------------------------------------------------


def test_is_deprecated_detects_deprecated_lifecycle() -> None:
    deprecated = attach_inventory(_item(), InventoryMetadata(lifecycle="deprecated"))
    assert is_deprecated(deprecated)


@pytest.mark.parametrize("lifecycle", ["experimental", "active", "blocked", None])
def test_is_deprecated_is_false_for_other_or_unknown_lifecycles(lifecycle: str | None) -> None:
    annotated = attach_inventory(
        _item(),
        InventoryMetadata(lifecycle=lifecycle),  # type: ignore[arg-type]
    )
    assert not is_deprecated(annotated)


def test_is_deprecated_is_false_without_inventory() -> None:
    assert not is_deprecated(_item())

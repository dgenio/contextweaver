"""Catalog inventory metadata for :class:`~contextweaver.types.SelectableItem` (issue #377).

Large tool catalogs accumulate governance questions — who owns a tool, how
risky it is, whether it is deprecated — that do not belong on the routing
fields of :class:`~contextweaver.types.SelectableItem` itself.
:class:`InventoryMetadata` is an optional, purely-declarative envelope for
that information.  Every field defaults to ``None`` ("unknown"), so existing
catalogs are unaffected and partially-annotated catalogs stay valid.

Storage convention
------------------

Inventory metadata is stored under the **reserved** contextweaver namespace
``item.metadata["_contextweaver"]["inventory"]`` (see
``docs/agent-context/invariants.md`` — the namespace round-trips through the
weaver-spec adapter and must never be repurposed).  Because it lives inside
``metadata``, it round-trips through ``SelectableItem.to_dict()`` /
``from_dict()`` untouched, with no change to the item serde.

Note that :attr:`InventoryMetadata.side_effects` (a graded
``none``/``read``/``write``/``destructive`` classification) is governance
metadata and is deliberately distinct from the boolean routing field
``SelectableItem.side_effects``; attaching an inventory never modifies the
routing field.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Any, Literal

from contextweaver.exceptions import CatalogError, ConfigError
from contextweaver.types import SelectableItem

#: Reserved metadata key owned by contextweaver (invariants.md); inventory
#: payloads nest under it rather than claiming a second top-level key.
CW_METADATA_KEY = "_contextweaver"

#: Sub-key of :data:`CW_METADATA_KEY` holding the inventory payload.
INVENTORY_KEY = "inventory"

#: Allowed values for :attr:`InventoryMetadata.risk_level`.
RISK_LEVELS: tuple[str, ...] = ("low", "medium", "high")

#: Allowed values for :attr:`InventoryMetadata.side_effects`.
SIDE_EFFECT_LEVELS: tuple[str, ...] = ("none", "read", "write", "destructive")

#: Allowed values for :attr:`InventoryMetadata.lifecycle`.
LIFECYCLES: tuple[str, ...] = ("experimental", "active", "deprecated", "blocked")

#: Allowed values for :attr:`InventoryMetadata.environment`.
ENVIRONMENTS: tuple[str, ...] = ("dev", "staging", "prod")

#: Allowed values for :attr:`InventoryMetadata.service_tier`.
SERVICE_TIERS: tuple[str, ...] = ("best_effort", "supported", "critical")

_STRING_FIELDS: tuple[str, ...] = ("owner_team", "business_domain", "contact", "source_repo")

_ENUM_FIELDS: dict[str, tuple[str, ...]] = {
    "risk_level": RISK_LEVELS,
    "side_effects": SIDE_EFFECT_LEVELS,
    "lifecycle": LIFECYCLES,
    "environment": ENVIRONMENTS,
    "service_tier": SERVICE_TIERS,
}


@dataclass(frozen=True)
class InventoryMetadata:
    """Governance / inventory metadata for one catalog item (issue #377).

    All fields default to ``None``, meaning "unknown" — absence carries no
    judgment, so existing catalogs are unaffected.  Enum-valued fields are
    validated at construction time and raise
    :class:`~contextweaver.exceptions.ConfigError` on values outside the
    documented vocabulary.

    Attributes:
        owner_team: Team responsible for the tool (free-form).
        business_domain: Business domain the tool belongs to (free-form).
        contact: Contact point for the owning team (free-form).
        source_repo: Repository the tool's implementation lives in (free-form).
        risk_level: Graded risk classification (:data:`RISK_LEVELS`).
        side_effects: Graded side-effect classification
            (:data:`SIDE_EFFECT_LEVELS`); distinct from the boolean
            ``SelectableItem.side_effects`` routing field.
        lifecycle: Lifecycle stage (:data:`LIFECYCLES`).
        environment: Deployment environment (:data:`ENVIRONMENTS`).
        service_tier: Support tier (:data:`SERVICE_TIERS`).
    """

    owner_team: str | None = None
    business_domain: str | None = None
    contact: str | None = None
    source_repo: str | None = None
    risk_level: Literal["low", "medium", "high"] | None = None
    side_effects: Literal["none", "read", "write", "destructive"] | None = None
    lifecycle: Literal["experimental", "active", "deprecated", "blocked"] | None = None
    environment: Literal["dev", "staging", "prod"] | None = None
    service_tier: Literal["best_effort", "supported", "critical"] | None = None

    def __post_init__(self) -> None:
        for name, allowed in _ENUM_FIELDS.items():
            value = getattr(self, name)
            if value is not None and value not in allowed:
                raise ConfigError(
                    f"invalid inventory.{name} value {value!r}",
                    hint=f"allowed values: {', '.join(allowed)} (or omit for unknown)",
                )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict, omitting ``None`` fields."""
        return {
            f.name: getattr(self, f.name) for f in fields(self) if getattr(self, f.name) is not None
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InventoryMetadata:
        """Deserialise from a JSON-compatible dict (missing keys mean unknown).

        Raises:
            ConfigError: On unknown keys, non-string values, or enum values
                outside the documented vocabulary.
        """
        return validate_inventory(data)


def validate_inventory(mapping: dict[str, Any]) -> InventoryMetadata:
    """Validate *mapping* and build an :class:`InventoryMetadata` from it.

    Strict by design: unknown keys are rejected so a typo (``owner`` for
    ``owner_team``) fails loudly instead of silently annotating nothing.

    Args:
        mapping: A JSON-compatible dict of inventory fields.

    Returns:
        The validated :class:`InventoryMetadata`.

    Raises:
        ConfigError: When *mapping* is not a dict, contains unknown keys,
            a non-string value for a string field, or an enum value outside
            the documented vocabulary (the hint lists the allowed values).
    """
    if not isinstance(mapping, dict):
        raise ConfigError(f"inventory metadata must be a mapping, got {type(mapping).__name__}")
    known = set(_STRING_FIELDS) | set(_ENUM_FIELDS)
    unknown = sorted(set(mapping) - known)
    if unknown:
        raise ConfigError(
            f"unknown inventory field(s): {', '.join(unknown)}",
            hint=f"allowed fields: {', '.join(sorted(known))}",
        )
    kwargs: dict[str, Any] = {}
    for name in _STRING_FIELDS:
        value = mapping.get(name)
        if value is not None and not isinstance(value, str):
            raise ConfigError(
                f"inventory.{name} must be a string or null, got {value!r}",
                hint="string fields are free-form; omit them when unknown",
            )
        kwargs[name] = value
    for name in _ENUM_FIELDS:
        kwargs[name] = mapping.get(name)
    return InventoryMetadata(**kwargs)  # __post_init__ validates the enum fields


def attach_inventory(item: SelectableItem, inventory: InventoryMetadata) -> SelectableItem:
    """Return a copy of *item* carrying *inventory* in its reserved metadata.

    The payload is stored under
    ``metadata["_contextweaver"]["inventory"]``.  Neither *item* nor any dict
    it shares is mutated — the item, its ``metadata``, and the reserved
    namespace dict are all copied.  Other keys already present under the
    reserved namespace are preserved; an existing inventory payload is
    replaced (attaching is an explicit write).

    Args:
        item: The catalog item to annotate.
        inventory: The inventory metadata to attach.

    Returns:
        A new :class:`~contextweaver.types.SelectableItem` with the inventory
        attached.

    Raises:
        CatalogError: When ``metadata["_contextweaver"]`` exists but is not a
            mapping — the reserved namespace must never be silently clobbered
            (``docs/agent-context/invariants.md``).
    """
    metadata = dict(item.metadata)
    existing = metadata.get(CW_METADATA_KEY)
    if existing is None:
        namespace: dict[str, Any] = {}
    elif isinstance(existing, dict):
        namespace = dict(existing)
    else:
        raise CatalogError(
            f"metadata[{CW_METADATA_KEY!r}] on item {item.id!r} is reserved and must "
            f"be a mapping, got {type(existing).__name__}",
            hint="see the reserved-namespace rule in docs/agent-context/invariants.md",
        )
    namespace[INVENTORY_KEY] = inventory.to_dict()
    metadata[CW_METADATA_KEY] = namespace
    return replace(item, metadata=metadata)


def inventory_of(item: SelectableItem) -> InventoryMetadata | None:
    """Return the inventory metadata attached to *item*, or ``None``.

    Args:
        item: The catalog item to read.

    Returns:
        The validated :class:`InventoryMetadata`, or ``None`` when the item
        carries no inventory payload (including when the reserved namespace
        is absent or not a mapping).

    Raises:
        ConfigError: When an inventory payload is present but invalid — a
            corrupt payload fails loudly rather than reading as "unknown".
    """
    namespace = item.metadata.get(CW_METADATA_KEY)
    if not isinstance(namespace, dict):
        return None
    payload = namespace.get(INVENTORY_KEY)
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise ConfigError(
            f"inventory payload on item {item.id!r} must be a mapping, got {type(payload).__name__}"
        )
    return validate_inventory(payload)


def is_deprecated(item: SelectableItem) -> bool:
    """Whether *item* is inventory-annotated with ``lifecycle="deprecated"``.

    Items without inventory metadata (or with an unknown lifecycle) are *not*
    deprecated — absence carries no judgment.
    """
    inventory = inventory_of(item)
    return inventory is not None and inventory.lifecycle == "deprecated"

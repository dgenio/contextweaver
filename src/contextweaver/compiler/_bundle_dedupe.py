"""Private dedupe helpers for compiler bundle construction."""

from __future__ import annotations

from contextweaver.compiler.resources import ResourceDescriptor
from contextweaver.compiler.sources import CapabilitySourceSnapshot
from contextweaver.exceptions import ValidationError
from contextweaver.types import SelectableItem


def dedupe_capabilities(snapshots: list[CapabilitySourceSnapshot]) -> list[SelectableItem]:
    """Return sorted capabilities, rejecting conflicting duplicate IDs."""
    by_id: dict[str, SelectableItem] = {}
    for snapshot in snapshots:
        for item in snapshot.capabilities:
            existing = by_id.get(item.id)
            if existing is not None and existing.to_dict() != item.to_dict():
                raise ValidationError(f"conflicting capability snapshot for {item.id!r}")
            by_id[item.id] = item
    return [by_id[key] for key in sorted(by_id)]


def dedupe_resources(snapshots: list[CapabilitySourceSnapshot]) -> list[ResourceDescriptor]:
    """Return sorted resources, rejecting conflicting duplicate IDs."""
    by_id: dict[str, ResourceDescriptor] = {}
    for snapshot in snapshots:
        for resource in snapshot.resources:
            existing = by_id.get(resource.resource_id)
            if existing is not None and existing.to_dict() != resource.to_dict():
                raise ValidationError(
                    f"conflicting resource descriptor for {resource.resource_id!r}"
                )
            by_id[resource.resource_id] = resource
    return [by_id[key] for key in sorted(by_id)]

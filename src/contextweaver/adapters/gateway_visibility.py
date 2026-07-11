"""Audience-scoped catalog visibility profiles for the MCP gateway (issue #379).

Where :mod:`contextweaver.adapters.gateway_authz` gates *execution* of a tool
that has already been routed, a :class:`VisibilityProfile` gates *exposure*: it
decides which catalog items an audience may see at all, before routing.  Like
``ToolPolicy``, everything here is pure and deterministic (case-sensitive
:func:`fnmatch.fnmatchcase` globs), all defaults are inert (an empty profile
hides nothing), and malformed config fails fast with
:class:`~contextweaver.exceptions.ConfigError`.

**Inventory seam (issue #377).**  Profiles read governance fields
(``business_domain``, ``risk_level``, ``side_effects``, ``lifecycle``,
``environment``) defensively as plain values from
``item.metadata["_contextweaver"]["inventory"]``.  This module deliberately
imports no typed inventory dataclass — issue #377 may formalise one, and the
seam can be tightened later without changing profile semantics.  Missing or
non-string values are treated as *unknown*.

**Unknown-metadata asymmetry (stated once, applied everywhere):**

- **Exclude rules fail open on unknown.**  ``exclude_domains`` /
  ``exclude_risk_levels`` / ``exclude_lifecycles`` never hide an item whose
  corresponding inventory value is unknown — an exclude list can only match a
  value that is actually present.
- **Allow rules fail closed on unknown.**  ``include_domains``,
  ``allow_side_effects``, and ``environments`` are allowlists; an item whose
  corresponding value is unknown is hidden, because an allowlist that can be
  bypassed by omitting metadata would be a security hole.

Namespace rules always apply (``SelectableItem.namespace`` is never unknown,
merely possibly empty): an item with no inventory metadata is governed by the
namespace/domain include/exclude rules only, plus any allowlists (which reject
it per the asymmetry above).
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from fnmatch import fnmatchcase
from typing import Any

from contextweaver.exceptions import ConfigError
from contextweaver.types import SelectableItem

#: Metadata namespace and key of the inventory seam (issue #377).
_METADATA_NAMESPACE = "_contextweaver"
_INVENTORY_KEY = "inventory"


def _inventory(item: SelectableItem) -> dict[str, Any]:
    """Return the inventory mapping from *item*'s metadata, or ``{}``."""
    meta = item.metadata.get(_METADATA_NAMESPACE)
    if not isinstance(meta, dict):
        return {}
    inventory = meta.get(_INVENTORY_KEY)
    return inventory if isinstance(inventory, dict) else {}


def _inventory_str(inventory: dict[str, Any], key: str) -> str | None:
    """Return the string value of *key*, or ``None`` when absent/non-string."""
    value = inventory.get(key)
    return value if isinstance(value, str) else None


def _glob_any(value: str, patterns: list[str]) -> bool:
    """Return ``True`` when *value* matches any glob in *patterns* (case-sensitive)."""
    return any(fnmatchcase(value, pattern) for pattern in patterns)


def _str_list(data: dict[str, Any], key: str) -> list[str]:
    """Validate and return ``data[key]`` as a list of strings (default ``[]``)."""
    raw = data.get(key) if data.get(key) is not None else []
    # A str/bytes is iterable, so ``exclude_domains: billing`` would become
    # per-character patterns — reject it explicitly (gateway_authz pattern).
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        raise ConfigError(f"VisibilityProfile.{key} must be a list of strings, got {raw!r}")
    return [str(value) for value in raw]


def _opt_str_list(data: dict[str, Any], key: str) -> list[str] | None:
    """Like :func:`_str_list` but preserves an absent key as ``None``."""
    return None if data.get(key) is None else _str_list(data, key)


@dataclass(frozen=True)
class VisibilityDecision:
    """The outcome of evaluating a :class:`VisibilityProfile` against one item.

    Attributes:
        allowed: ``True`` when the item is visible under the profile.
        reason: Short, human-readable justification (safe for diagnostics —
            derived from catalog metadata, never from tool arguments).
    """

    allowed: bool
    reason: str


@dataclass
class VisibilityProfile:
    """One named audience's view over a tool catalog (issue #379).

    Every field defaults to inert: an all-defaults profile makes every item
    visible.  List fields containing globs use :func:`fnmatch.fnmatchcase`
    (exact strings match exactly).  See the module docstring for the
    fail-open/fail-closed asymmetry on unknown inventory metadata.

    Attributes:
        name: Profile name (the key of the ``profiles:`` config block).
        include_domains: When non-empty, only items whose inventory
            ``business_domain`` matches a glob are visible (fail-closed).
        exclude_domains: Hide items whose known ``business_domain`` matches.
        include_namespaces: When non-empty, only items whose ``namespace``
            matches a glob are visible.
        exclude_namespaces: Hide items whose ``namespace`` matches a glob.
        exclude_risk_levels: Hide items whose known inventory ``risk_level``
            is listed (exact match, e.g. ``"high"``).
        allow_side_effects: When set, only items whose inventory
            ``side_effects`` value is listed are visible; unknown is rejected
            (fail-closed allowlist).  ``None`` allows all.
        exclude_lifecycles: Hide items whose known inventory ``lifecycle`` is
            listed (e.g. ``"deprecated"``, ``"blocked"``).  Default empty —
            deliberately inert, per repo convention.
        environments: When set, only items whose inventory ``environment``
            value is listed are visible; unknown is rejected (fail-closed
            allowlist).  ``None`` allows all.
    """

    name: str
    include_domains: list[str] = field(default_factory=list)
    exclude_domains: list[str] = field(default_factory=list)
    include_namespaces: list[str] = field(default_factory=list)
    exclude_namespaces: list[str] = field(default_factory=list)
    exclude_risk_levels: list[str] = field(default_factory=list)
    allow_side_effects: list[str] | None = None
    exclude_lifecycles: list[str] = field(default_factory=list)
    environments: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON/YAML-compatible dict (omitting inert fields)."""
        out: dict[str, Any] = {"name": self.name}
        for key in sorted(_PROFILE_KEYS - {"name"}):
            values = getattr(self, key)
            if values is not None and (values or key in ("allow_side_effects", "environments")):
                out[key] = list(values)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VisibilityProfile:
        """Deserialise from a config dict, validating keys, shape, and types.

        Fails fast with :class:`~contextweaver.exceptions.ConfigError` on
        unknown keys (a typoed ``exclud_domains`` must not silently weaken a
        profile), a missing/blank ``name``, or non-list field values.
        """
        if not isinstance(data, dict):
            raise ConfigError(f"VisibilityProfile entry must be a mapping, got {data!r}")
        unknown = sorted(set(data) - _PROFILE_KEYS)
        if unknown:
            raise ConfigError(f"VisibilityProfile got unknown keys: {unknown}")
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"VisibilityProfile.name must be a non-empty string, got {name!r}")
        return cls(
            name=name,
            include_domains=_str_list(data, "include_domains"),
            exclude_domains=_str_list(data, "exclude_domains"),
            include_namespaces=_str_list(data, "include_namespaces"),
            exclude_namespaces=_str_list(data, "exclude_namespaces"),
            exclude_risk_levels=_str_list(data, "exclude_risk_levels"),
            allow_side_effects=_opt_str_list(data, "allow_side_effects"),
            exclude_lifecycles=_str_list(data, "exclude_lifecycles"),
            environments=_opt_str_list(data, "environments"),
        )


#: Config keys accepted by :meth:`VisibilityProfile.from_dict` — exactly the field names.
_PROFILE_KEYS = frozenset(spec.name for spec in fields(VisibilityProfile))


def evaluate_visibility(profile: VisibilityProfile, item: SelectableItem) -> VisibilityDecision:
    """Decide whether *item* is visible under *profile*.

    Rules are checked in a fixed order (namespace excludes/includes, domain
    excludes/includes, risk, lifecycle, side-effect allowlist, environments)
    and the first failing rule determines the reason.  Inventory fields are
    read defensively from ``item.metadata["_contextweaver"]["inventory"]``;
    exclude rules fail open on unknown values while allowlist rules
    (``include_domains`` / ``allow_side_effects`` / ``environments``) fail
    closed — see the module docstring for the rationale.

    Args:
        profile: The audience profile to evaluate.
        item: The catalog item under consideration.

    Returns:
        A :class:`VisibilityDecision` with ``allowed`` and a stable reason.
    """
    namespace = item.namespace
    if profile.exclude_namespaces and _glob_any(namespace, profile.exclude_namespaces):
        return VisibilityDecision(False, f"namespace {namespace!r} matches exclude_namespaces")
    if profile.include_namespaces and not _glob_any(namespace, profile.include_namespaces):
        return VisibilityDecision(False, f"namespace {namespace!r} not in include_namespaces")
    inventory = _inventory(item)
    domain = _inventory_str(inventory, "business_domain")
    if (
        profile.exclude_domains
        and domain is not None
        and _glob_any(domain, profile.exclude_domains)
    ):
        return VisibilityDecision(False, f"domain {domain!r} matches exclude_domains")
    if profile.include_domains and (
        domain is None or not _glob_any(domain, profile.include_domains)
    ):
        return VisibilityDecision(False, f"domain {domain!r} not in include_domains")
    risk = _inventory_str(inventory, "risk_level")
    if risk is not None and risk in profile.exclude_risk_levels:
        return VisibilityDecision(False, f"risk_level {risk!r} is excluded")
    lifecycle = _inventory_str(inventory, "lifecycle")
    if lifecycle is not None and lifecycle in profile.exclude_lifecycles:
        return VisibilityDecision(False, f"lifecycle {lifecycle!r} is excluded")
    if profile.allow_side_effects is not None:
        side_effects = _inventory_str(inventory, "side_effects")
        if side_effects is None or side_effects not in profile.allow_side_effects:
            return VisibilityDecision(
                False, f"side_effects {side_effects!r} not in allow_side_effects"
            )
    if profile.environments is not None:
        environment = _inventory_str(inventory, "environment")
        if environment is None or environment not in profile.environments:
            return VisibilityDecision(False, f"environment {environment!r} not in environments")
    return VisibilityDecision(True, f"visible under profile {profile.name!r}")


def filter_catalog(
    profile: VisibilityProfile,
    items: list[SelectableItem],
) -> tuple[list[SelectableItem], list[tuple[str, str]]]:
    """Split *items* into (visible, denied) under *profile*.

    Args:
        profile: The audience profile to apply.
        items: Catalog items, evaluated and returned in input order.

    Returns:
        ``(visible, denied)`` — visible items in input order (never cloned or
        mutated), and ``(item_id, reason)`` pairs for every hidden item so the
        loss is auditable rather than silent.
    """
    visible: list[SelectableItem] = []
    denied: list[tuple[str, str]] = []
    for item in items:
        decision = evaluate_visibility(profile, item)
        if decision.allowed:
            visible.append(item)
        else:
            denied.append((item.id, decision.reason))
    return visible, denied


def parse_profiles(mapping: dict[str, Any]) -> dict[str, VisibilityProfile]:
    """Parse a ``profiles:`` config block into named profiles (issue #379).

    Args:
        mapping: ``{profile_name: {include_domains: [...], ...}}``.  Each block
            takes its name from its key; a ``name`` field inside the block must
            match the key when present.

    Returns:
        A dict of profiles keyed (and named) by profile name, built in sorted
        key order for determinism.

    Raises:
        ConfigError: On a non-mapping block, a ``name`` mismatch, or any
            :meth:`VisibilityProfile.from_dict` validation failure.
    """
    if not isinstance(mapping, dict):
        raise ConfigError(f"profiles block must be a mapping, got {mapping!r}")
    profiles: dict[str, VisibilityProfile] = {}
    for key in sorted(mapping, key=str):
        block = mapping[key]
        if not isinstance(block, dict):
            raise ConfigError(f"profile {key!r} must be a mapping, got {block!r}")
        declared = block.get("name")
        if declared is not None and declared != key:
            raise ConfigError(f"profile {key!r} declares mismatched name {declared!r}")
        profiles[str(key)] = VisibilityProfile.from_dict({**block, "name": str(key)})
    return profiles


__all__ = [
    "VisibilityDecision",
    "VisibilityProfile",
    "evaluate_visibility",
    "filter_catalog",
    "parse_profiles",
]

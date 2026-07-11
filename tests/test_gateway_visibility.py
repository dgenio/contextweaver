"""Tests for contextweaver.adapters.gateway_visibility (issue #379).

Covers the pure profile layer: include/exclude namespace and domain rules,
risk/lifecycle exclusion, the side-effect and environment allowlists (incl.
the fail-closed-on-unknown asymmetry), catalog filtering with recorded denial
reasons, config parsing, and serde round-trips.
"""

from __future__ import annotations

import pytest

from contextweaver.adapters.gateway_visibility import (
    VisibilityProfile,
    evaluate_visibility,
    filter_catalog,
    parse_profiles,
)
from contextweaver.exceptions import ConfigError
from contextweaver.types import SelectableItem


def _item(
    item_id: str = "github:create_issue",
    namespace: str = "github",
    inventory: dict[str, object] | None = None,
) -> SelectableItem:
    metadata: dict[str, object] = {}
    if inventory is not None:
        metadata = {"_contextweaver": {"inventory": inventory}}
    return SelectableItem(
        id=item_id,
        kind="tool",
        name=item_id.rsplit(":", 1)[-1],
        description="does a thing",
        namespace=namespace,
        metadata=dict(metadata),
    )


# ---------------------------------------------------------------------------
# Inert defaults
# ---------------------------------------------------------------------------


def test_empty_profile_is_inert() -> None:
    profile = VisibilityProfile(name="everyone")
    for item in (
        _item(),
        _item("fs:delete_file", namespace="fs"),
        _item(inventory={"risk_level": "high", "lifecycle": "deprecated"}),
    ):
        decision = evaluate_visibility(profile, item)
        assert decision.allowed is True
        assert "everyone" in decision.reason


# ---------------------------------------------------------------------------
# Namespace rules
# ---------------------------------------------------------------------------


def test_include_namespaces_glob() -> None:
    profile = VisibilityProfile(name="p", include_namespaces=["github", "billing-*"])
    assert evaluate_visibility(profile, _item()).allowed is True
    assert evaluate_visibility(profile, _item(namespace="billing-eu")).allowed is True
    denied = evaluate_visibility(profile, _item(namespace="fs"))
    assert denied.allowed is False
    assert "include_namespaces" in denied.reason


def test_exclude_namespaces_glob() -> None:
    profile = VisibilityProfile(name="p", exclude_namespaces=["fs*"])
    assert evaluate_visibility(profile, _item()).allowed is True
    denied = evaluate_visibility(profile, _item(namespace="fs-internal"))
    assert denied.allowed is False
    assert "exclude_namespaces" in denied.reason


# ---------------------------------------------------------------------------
# Domain rules
# ---------------------------------------------------------------------------


def test_include_domains_matches_known_domain() -> None:
    profile = VisibilityProfile(name="p", include_domains=["billing", "payments-*"])
    assert evaluate_visibility(profile, _item(inventory={"business_domain": "billing"})).allowed
    assert evaluate_visibility(profile, _item(inventory={"business_domain": "payments-eu"})).allowed
    assert evaluate_visibility(profile, _item(inventory={"business_domain": "hr"})).allowed is False


def test_include_domains_fails_closed_on_unknown_domain() -> None:
    # An allowlist that could be bypassed by omitting metadata would be a hole.
    profile = VisibilityProfile(name="p", include_domains=["billing"])
    decision = evaluate_visibility(profile, _item(inventory=None))
    assert decision.allowed is False
    assert "include_domains" in decision.reason


def test_exclude_domains_fails_open_on_unknown_domain() -> None:
    profile = VisibilityProfile(name="p", exclude_domains=["billing"])
    assert evaluate_visibility(profile, _item(inventory=None)).allowed is True
    assert (
        evaluate_visibility(profile, _item(inventory={"business_domain": "billing"})).allowed
        is False
    )


# ---------------------------------------------------------------------------
# Risk / lifecycle exclusion (fail-open on unknown)
# ---------------------------------------------------------------------------


def test_exclude_risk_levels() -> None:
    profile = VisibilityProfile(name="p", exclude_risk_levels=["high"])
    assert evaluate_visibility(profile, _item(inventory={"risk_level": "low"})).allowed is True
    denied = evaluate_visibility(profile, _item(inventory={"risk_level": "high"}))
    assert denied.allowed is False
    assert "risk_level" in denied.reason
    # Unknown risk is not excluded (exclude rules fail open).
    assert evaluate_visibility(profile, _item(inventory={})).allowed is True


def test_exclude_lifecycles() -> None:
    profile = VisibilityProfile(name="p", exclude_lifecycles=["deprecated", "blocked"])
    assert evaluate_visibility(profile, _item(inventory={"lifecycle": "active"})).allowed is True
    assert (
        evaluate_visibility(profile, _item(inventory={"lifecycle": "deprecated"})).allowed is False
    )
    assert evaluate_visibility(profile, _item(inventory=None)).allowed is True


def test_exclude_rules_ignore_non_string_inventory_values() -> None:
    profile = VisibilityProfile(name="p", exclude_risk_levels=["high"])
    # A malformed (non-string) value is unknown, not excluded.
    assert evaluate_visibility(profile, _item(inventory={"risk_level": 3})).allowed is True


# ---------------------------------------------------------------------------
# Side-effect allowlist (fail-closed on unknown)
# ---------------------------------------------------------------------------


def test_allow_side_effects_allowlist() -> None:
    profile = VisibilityProfile(name="p", allow_side_effects=["none", "read"])
    assert evaluate_visibility(profile, _item(inventory={"side_effects": "read"})).allowed is True
    denied = evaluate_visibility(profile, _item(inventory={"side_effects": "destructive"}))
    assert denied.allowed is False
    assert "allow_side_effects" in denied.reason


def test_allow_side_effects_rejects_unknown() -> None:
    # Fail-closed: omitting side-effect metadata must not bypass the allowlist.
    profile = VisibilityProfile(name="p", allow_side_effects=["none"])
    denied = evaluate_visibility(profile, _item(inventory=None))
    assert denied.allowed is False
    assert "allow_side_effects" in denied.reason


def test_allow_side_effects_none_allows_all() -> None:
    profile = VisibilityProfile(name="p", allow_side_effects=None)
    assert (
        evaluate_visibility(profile, _item(inventory={"side_effects": "destructive"})).allowed
        is True
    )


# ---------------------------------------------------------------------------
# Environments (fail-closed allowlist)
# ---------------------------------------------------------------------------


def test_environments_matching() -> None:
    profile = VisibilityProfile(name="p", environments=["prod"])
    assert evaluate_visibility(profile, _item(inventory={"environment": "prod"})).allowed is True
    assert evaluate_visibility(profile, _item(inventory={"environment": "dev"})).allowed is False
    # Unknown environment is rejected (allowlist semantics).
    assert evaluate_visibility(profile, _item(inventory=None)).allowed is False


# ---------------------------------------------------------------------------
# filter_catalog
# ---------------------------------------------------------------------------


def test_filter_catalog_preserves_order_and_records_reasons() -> None:
    profile = VisibilityProfile(name="p", exclude_namespaces=["fs"], exclude_risk_levels=["high"])
    items = [
        _item("github:one"),
        _item("fs:two", namespace="fs"),
        _item("github:three", inventory={"risk_level": "high"}),
        _item("github:four"),
    ]
    visible, denied = filter_catalog(profile, items)
    assert [item.id for item in visible] == ["github:one", "github:four"]
    assert [item_id for item_id, _ in denied] == ["fs:two", "github:three"]
    assert "exclude_namespaces" in denied[0][1]
    assert "risk_level" in denied[1][1]


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------


def test_parse_profiles_builds_named_profiles() -> None:
    profiles = parse_profiles(
        {
            "support": {"include_domains": ["billing"], "exclude_risk_levels": ["high"]},
            "ops": {"allow_side_effects": ["none", "read"], "environments": ["prod"]},
        }
    )
    assert sorted(profiles) == ["ops", "support"]
    assert profiles["support"].name == "support"
    assert profiles["support"].include_domains == ["billing"]
    assert profiles["ops"].allow_side_effects == ["none", "read"]
    assert profiles["ops"].environments == ["prod"]


def test_parse_profiles_rejects_non_mapping_block() -> None:
    with pytest.raises(ConfigError):
        parse_profiles({"support": ["billing"]})


def test_parse_profiles_rejects_mismatched_name() -> None:
    with pytest.raises(ConfigError):
        parse_profiles({"support": {"name": "other"}})


def test_from_dict_rejects_unknown_keys() -> None:
    # A typoed key must not silently weaken a profile.
    with pytest.raises(ConfigError):
        VisibilityProfile.from_dict({"name": "p", "exclud_domains": ["billing"]})


def test_from_dict_rejects_string_list_fields() -> None:
    # A bare string would otherwise iterate into per-character globs.
    with pytest.raises(ConfigError):
        VisibilityProfile.from_dict({"name": "p", "exclude_domains": "billing"})
    with pytest.raises(ConfigError):
        VisibilityProfile.from_dict({"name": "p", "allow_side_effects": "none"})


def test_from_dict_rejects_missing_name_and_non_mapping() -> None:
    with pytest.raises(ConfigError):
        VisibilityProfile.from_dict({"include_domains": ["billing"]})
    with pytest.raises(ConfigError):
        VisibilityProfile.from_dict(["not", "a", "mapping"])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Serde
# ---------------------------------------------------------------------------


def test_roundtrip_preserves_profile() -> None:
    profile = VisibilityProfile(
        name="support",
        include_domains=["billing", "payments-*"],
        exclude_namespaces=["fs*"],
        exclude_risk_levels=["high"],
        allow_side_effects=["none", "read"],
        exclude_lifecycles=["deprecated"],
        environments=["prod"],
    )
    restored = VisibilityProfile.from_dict(profile.to_dict())
    assert restored == profile
    assert restored.to_dict() == profile.to_dict()


def test_to_dict_omits_inert_fields() -> None:
    assert VisibilityProfile(name="p").to_dict() == {"name": "p"}
    # An explicit empty allowlist is not inert (it hides everything) — kept.
    out = VisibilityProfile(name="p", allow_side_effects=[], environments=[]).to_dict()
    assert out == {"name": "p", "allow_side_effects": [], "environments": []}

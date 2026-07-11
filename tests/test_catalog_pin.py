"""Tests for catalog pinning (issue #656)."""

from __future__ import annotations

import pytest

from contextweaver.adapters.catalog_pin import (
    PIN_MODES,
    PinCheck,
    PinPolicy,
    check_catalog_pin,
    enforce_pin,
)
from contextweaver.exceptions import ConfigError
from contextweaver.routing.manifest import compute_catalog_hash
from contextweaver.types import SelectableItem


def _items() -> list[SelectableItem]:
    return [
        SelectableItem(
            id="github.create_issue",
            kind="tool",
            name="create_issue",
            description="Create an issue.",
            namespace="github",
            tags=["write"],
        ),
        SelectableItem(
            id="github.list_issues",
            kind="tool",
            name="list_issues",
            description="List issues.",
            namespace="github",
            tags=["read"],
        ),
        SelectableItem(
            id="jira.delete_issue",
            kind="tool",
            name="delete_issue",
            description="Delete an issue.",
            namespace="jira",
            tags=["write"],
        ),
    ]


def test_hash_is_invariant_under_reordering() -> None:
    items = _items()
    assert compute_catalog_hash(items) == compute_catalog_hash(list(reversed(items)))


def test_hash_ignores_metadata_and_examples_but_not_routing_fields() -> None:
    items = _items()
    baseline = compute_catalog_hash(items)

    items[0].metadata["runtime"] = "changed"
    items[0].examples.append("new example")
    assert compute_catalog_hash(items) == baseline

    items[0].description = "Create an issue (drifted)."
    assert compute_catalog_hash(items) != baseline


def test_check_catalog_pin_match_and_mismatch() -> None:
    items = _items()
    pinned = compute_catalog_hash(items)

    check = check_catalog_pin(PinPolicy(expected_hash=pinned), items)
    assert check.matched is True
    assert check.expected_hash == check.actual_hash == pinned
    assert check.mode == "warn"
    assert "ok" in check.message

    check = check_catalog_pin(PinPolicy(expected_hash="0" * 64, mode="strict"), items)
    assert check.matched is False
    assert check.actual_hash == pinned
    assert "0" * 64 in check.message
    assert pinned in check.message
    assert "mismatch" in check.message


@pytest.mark.parametrize("mode", sorted(PIN_MODES))
def test_enforce_pin_is_noop_on_match(mode: str) -> None:
    items = _items()
    policy = PinPolicy(expected_hash=compute_catalog_hash(items), mode=mode)  # type: ignore[arg-type]
    enforce_pin(check_catalog_pin(policy, items))


def test_enforce_pin_warn_mode_never_raises_on_mismatch() -> None:
    check = check_catalog_pin(PinPolicy(expected_hash="0" * 64, mode="warn"), _items())
    enforce_pin(check)


def test_enforce_pin_strict_mode_raises_with_both_hashes_and_hint() -> None:
    items = _items()
    actual = compute_catalog_hash(items)
    check = check_catalog_pin(PinPolicy(expected_hash="0" * 64, mode="strict"), items)

    with pytest.raises(ConfigError) as excinfo:
        enforce_pin(check)

    rendered = str(excinfo.value)
    assert "0" * 64 in rendered
    assert actual in rendered
    assert "re-pin with the new hash or investigate tool-surface drift" in rendered


def test_pin_policy_serde_round_trip() -> None:
    policy = PinPolicy(expected_hash="a" * 64, mode="strict")
    assert policy.to_dict() == {"expected_hash": "a" * 64, "mode": "strict"}
    assert PinPolicy.from_dict(policy.to_dict()) == policy

    defaulted = PinPolicy.from_dict({"expected_hash": "b" * 64})
    assert defaulted.mode == "warn"


def test_pin_check_to_dict() -> None:
    check = PinCheck(matched=False, expected_hash="a" * 64, actual_hash="b" * 64, mode="strict")
    assert check.to_dict() == {
        "matched": False,
        "expected_hash": "a" * 64,
        "actual_hash": "b" * 64,
        "mode": "strict",
    }


def test_expected_hash_must_be_sha256_hex() -> None:
    # Wrong length, non-hex chars, and uppercase-that-does-not-normalise-to-hex
    # are all rejected so a typo cannot silently satisfy strict mode.
    for bad in ("deadbeef", "z" * 64, "a" * 63, "a" * 65, "not-a-real-hash"):
        with pytest.raises(ConfigError):
            PinPolicy(expected_hash=bad)


def test_expected_hash_is_normalised() -> None:
    # Surrounding whitespace/newlines and uppercase are normalised, not rejected.
    policy = PinPolicy(expected_hash=f"  {'A' * 64}\n")
    assert policy.expected_hash == "a" * 64
    assert PinPolicy.from_dict({"expected_hash": "A" * 64}).expected_hash == "a" * 64


def test_bad_config_raises_config_error() -> None:
    with pytest.raises(ConfigError):
        PinPolicy(expected_hash="")
    with pytest.raises(ConfigError):
        PinPolicy(expected_hash="  ")
    with pytest.raises(ConfigError):
        PinPolicy(expected_hash="a" * 64, mode="hard")  # type: ignore[arg-type]
    with pytest.raises(ConfigError):
        PinPolicy.from_dict({"mode": "warn"})
    with pytest.raises(ConfigError):
        PinPolicy.from_dict({"expected_hash": "a" * 64, "mode": "hard"})
    with pytest.raises(ConfigError):
        PinPolicy.from_dict({"expected_hash": "a" * 64, "modes": "warn"})
    with pytest.raises(ConfigError):
        PinPolicy.from_dict({"expected_hash": 42})
    with pytest.raises(ConfigError):
        PinPolicy.from_dict("not a mapping")  # type: ignore[arg-type]

"""Sensitivity / firewall regression fixtures (issue #292).

Drives a small set of explicit fixture items (public, internal,
confidential, restricted, PII-like, secret-like) through
``apply_sensitivity_filter`` at every sensitivity floor and asserts the
**existing** behavior:

* lower-sensitivity-than-floor items pass through
* at-or-above-floor items are dropped (default ``"drop"`` action) or
  redacted (``"redact"`` action)
* the per-item ``Sensitivity`` enum on the redacted output reflects the
  original level so dependency closure and stats are still coherent
* redaction *never* leaves the raw text in the result

The intent matches the project's security-grade posture
(``.claude/rules/sensitivity.md``): pin the conservative defaults, make
weakening obvious in review, and provide a public, hard-to-misread
regression surface.

These tests do not modify ``apply_sensitivity_filter`` or its defaults.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from contextweaver.config import ContextPolicy
from contextweaver.context.sensitivity import (
    MaskRedactionHook,
    apply_sensitivity_filter,
)
from contextweaver.types import ContextItem, ItemKind, Sensitivity
from tests.fixtures._normalize import load_fixture

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sensitivity"
_FIXTURES = sorted(FIXTURE_DIR.glob("*.json"))


_FLOORS: list[Sensitivity] = [
    Sensitivity.public,
    Sensitivity.internal,
    Sensitivity.confidential,
    Sensitivity.restricted,
]


def _build_item(raw: dict[str, Any]) -> ContextItem:
    metadata = cast("dict[str, Any]", raw.get("metadata") or {})
    return ContextItem(
        id=str(raw["id"]),
        kind=ItemKind(raw["kind"]),
        text=str(raw["text"]),
        sensitivity=Sensitivity(raw["sensitivity"]),
        metadata=dict(metadata),
    )


# ----------------------------------------------------------------------
# Fixture set sanity
# ----------------------------------------------------------------------


def test_sensitivity_fixture_set_covers_all_levels() -> None:
    """Every ``Sensitivity`` level (and the two security archetypes) must
    have at least one checked-in fixture."""
    items = [load_fixture(p)["item"] for p in _FIXTURES]
    levels = {i["sensitivity"] for i in items}
    required = {"public", "internal", "confidential", "restricted"}
    missing = required - levels
    assert not missing, f"missing sensitivity fixture levels: {sorted(missing)}"
    assert any(i.get("metadata", {}).get("secret_like") for i in items), (
        "no secret_like fixture present"
    )
    assert any(i.get("metadata", {}).get("pii_like") for i in items), "no pii_like fixture present"


def test_sensitivity_fixture_files_are_non_empty() -> None:
    assert _FIXTURES, f"no sensitivity fixtures under {FIXTURE_DIR}"


# ----------------------------------------------------------------------
# Drop-mode behaviour at each floor
# ----------------------------------------------------------------------


@pytest.mark.parametrize("fixture_path", _FIXTURES, ids=lambda p: p.stem)
@pytest.mark.parametrize("floor", _FLOORS, ids=lambda s: s.value)
def test_drop_mode_matches_expectations(fixture_path: Path, floor: Sensitivity) -> None:
    """At each floor the fixture's ``expected_actions`` map predicts
    pass / filtered behavior."""
    fixture = load_fixture(fixture_path)
    item = _build_item(fixture["item"])
    expected = fixture["expected_actions"][f"floor_{floor.value}"]

    policy = ContextPolicy(sensitivity_floor=floor, sensitivity_action="drop")
    kept, dropped = apply_sensitivity_filter([item], policy)
    if expected == "passes":
        assert kept == [item], (
            f"{fixture_path} at floor={floor.value}: expected pass-through, got "
            f"kept={kept!r}, dropped={dropped}"
        )
        assert dropped == 0
    elif expected == "filtered":
        assert kept == [], (
            f"{fixture_path} at floor={floor.value}: expected drop, got kept={kept!r}"
        )
        assert dropped == 1
    else:  # pragma: no cover — defends fixture format
        raise AssertionError(f"{fixture_path}: unknown expected_actions value {expected!r}")


# ----------------------------------------------------------------------
# Redact-mode never leaks raw text
# ----------------------------------------------------------------------


@pytest.mark.parametrize("fixture_path", _FIXTURES, ids=lambda p: p.stem)
def test_redact_mode_strips_raw_text_at_or_above_floor(fixture_path: Path) -> None:
    """When an item meets-or-exceeds the floor, redact mode replaces
    the raw text with a placeholder — the raw text must NOT appear in
    the output.

    Tested at ``floor=internal`` so the suite exercises both the
    pass-through path (public) and the redact path (internal+).
    """
    fixture = load_fixture(fixture_path)
    item = _build_item(fixture["item"])

    policy = ContextPolicy(
        sensitivity_floor=Sensitivity.internal,
        sensitivity_action="redact",
        redaction_hooks=["mask"],
    )
    kept, dropped = apply_sensitivity_filter([item], policy)

    # Redact mode keeps the item but masks the text.
    assert dropped == 0
    assert len(kept) == 1
    result = kept[0]

    if item.sensitivity is Sensitivity.public:
        # Below-floor item passes through unmodified.
        assert result.text == item.text
    else:
        # At-or-above-floor item: raw text must NOT survive.
        assert item.text not in result.text, (
            f"{fixture_path}: raw text leaked through redaction: {result.text!r}"
        )
        assert result.text == f"[REDACTED: {item.sensitivity.value}]"
        # ID + kind preserved so the item still participates in
        # dependency closure.
        assert result.id == item.id
        assert result.kind == item.kind


# ----------------------------------------------------------------------
# Defaults are not silently weakened
# ----------------------------------------------------------------------


def test_default_policy_drops_confidential_and_above() -> None:
    """Hard-coded regression: the default :class:`ContextPolicy`
    must drop ``confidential`` and ``restricted`` items.

    This is the contract that ``.claude/rules/sensitivity.md`` calls
    out as security-grade.  A change that weakens the default floor
    or action would make this test fail loudly.
    """
    policy = ContextPolicy()
    items = [
        _build_item(load_fixture(FIXTURE_DIR / "public.json")["item"]),
        _build_item(load_fixture(FIXTURE_DIR / "internal.json")["item"]),
        _build_item(load_fixture(FIXTURE_DIR / "confidential.json")["item"]),
        _build_item(load_fixture(FIXTURE_DIR / "restricted.json")["item"]),
    ]
    kept, dropped = apply_sensitivity_filter(items, policy)
    # Default floor is ``confidential`` — so confidential and restricted
    # are dropped; public and internal pass.
    kept_ids = {k.id for k in kept}
    assert kept_ids == {"pub-1", "int-1"}, f"unexpected kept set: {kept_ids}"
    assert dropped == 2


def test_mask_redaction_hook_preserves_item_id_and_kind() -> None:
    """``MaskRedactionHook`` must not change ``id`` or ``kind`` — those
    fields are load-bearing for dependency closure."""
    item = _build_item(load_fixture(FIXTURE_DIR / "restricted.json")["item"])
    redacted = MaskRedactionHook().redact(item)
    assert redacted.id == item.id
    assert redacted.kind == item.kind
    assert redacted.text == f"[REDACTED: {item.sensitivity.value}]"
    assert item.text not in redacted.text

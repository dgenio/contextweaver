"""Contract fixtures for weaver-spec payloads (issue #295).

Loads every checked-in fixture under ``tests/fixtures/weaver_spec/``,
maps it through the existing ``adapters.weaver_contracts`` adapter, and
asserts it round-trips losslessly.  When weaver-spec JSON schemas are
available locally (typically only true in CI, which fetches them via
``make weaver-conformance``) we additionally validate the spec-shaped
JSON against the schema.

The pytest surface complements ``scripts/weaver_spec_conformance.py``:
the script gates the *build* step (CI), pytest gates *development*
locally and surfaces actionable failure messages that include the
fixture file path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

weaver_contracts = pytest.importorskip("weaver_contracts")

from contextweaver.envelope import ChoiceCard, ResultEnvelope, RoutingDecision  # noqa: E402
from contextweaver.types import SelectableItem  # noqa: E402
from tests.fixtures._normalize import load_fixture  # noqa: E402

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "weaver_spec"

_FIXTURES = sorted(FIXTURE_DIR.glob("*.json"))


# ----------------------------------------------------------------------
# Fixture set sanity
# ----------------------------------------------------------------------


def test_fixture_set_covers_required_kinds() -> None:
    """The four payload types that weaver-spec exposes must all have
    at least one checked-in fixture."""
    kinds = {load_fixture(p)["kind"] for p in _FIXTURES}
    required = {"selectable_item", "choice_card", "routing_decision", "frame"}
    missing = required - kinds
    assert not missing, f"missing weaver-spec fixture kinds: {sorted(missing)}"


def test_fixture_set_is_non_empty() -> None:
    assert _FIXTURES, f"no weaver-spec fixtures under {FIXTURE_DIR}"


# ----------------------------------------------------------------------
# Per-fixture round-trip
# ----------------------------------------------------------------------


def _selectable_round_trip(payload: dict[str, object]) -> None:
    from contextweaver.adapters.weaver_contracts import (
        from_weaver_selectable_item,
        to_weaver_selectable_item,
    )

    item = SelectableItem.from_dict(payload)
    restored = from_weaver_selectable_item(to_weaver_selectable_item(item))
    assert restored == item


def _choice_card_round_trip(payload: dict[str, object]) -> None:
    from contextweaver.adapters.weaver_contracts import (
        from_weaver_choice_card_single,
        to_weaver_choice_card,
    )

    card = ChoiceCard.from_dict(payload)
    restored = from_weaver_choice_card_single(to_weaver_choice_card(card))
    assert restored == card


def _routing_decision_round_trip(payload: dict[str, object]) -> None:
    from contextweaver.adapters.weaver_contracts import (
        from_weaver_routing_decision,
        to_weaver_routing_decision,
    )

    decision = RoutingDecision.from_dict(payload)
    restored = from_weaver_routing_decision(to_weaver_routing_decision(decision))
    assert restored == decision


def _frame_round_trip(payload: dict[str, object]) -> None:
    from datetime import datetime, timezone

    from contextweaver.adapters.weaver_contracts import (
        from_weaver_frame,
        to_weaver_frame,
    )

    envelope_data = payload["envelope"]
    assert isinstance(envelope_data, dict)
    envelope = ResultEnvelope.from_dict(envelope_data)
    created_raw = payload.get("created_at")
    if isinstance(created_raw, str):
        created = datetime.fromisoformat(created_raw)
    else:
        created = datetime.now(timezone.utc)
    frame = to_weaver_frame(
        envelope,
        frame_id=str(payload["frame_id"]),
        capability_id=str(payload["capability_id"]),
        created_at=created,
    )
    restored = from_weaver_frame(frame)
    assert restored == envelope


_ROUND_TRIP_DISPATCH = {
    "selectable_item": _selectable_round_trip,
    "choice_card": _choice_card_round_trip,
    "routing_decision": _routing_decision_round_trip,
    "frame": _frame_round_trip,
}


@pytest.mark.parametrize("fixture_path", _FIXTURES, ids=lambda p: p.stem)
def test_weaver_spec_fixture_round_trips(fixture_path: Path) -> None:
    """Each fixture round-trips losslessly through the adapter."""
    fixture = load_fixture(fixture_path)
    kind = fixture.get("kind")
    payload = fixture.get("payload")
    label = fixture.get("label", fixture_path.name)
    assert kind in _ROUND_TRIP_DISPATCH, (
        f"{fixture_path}: unknown 'kind' {kind!r}; expected one of {sorted(_ROUND_TRIP_DISPATCH)}"
    )
    assert isinstance(payload, dict), f"{fixture_path}: 'payload' must be an object"
    try:
        _ROUND_TRIP_DISPATCH[kind](payload)
    except AssertionError as exc:
        raise AssertionError(
            f"weaver-spec round-trip drift in fixture {fixture_path} ({label}): {exc}"
        ) from exc


# ----------------------------------------------------------------------
# Schema validation (skipped when weaver-spec schemas aren't local)
# ----------------------------------------------------------------------


# We deliberately do not fetch schemas at test time — that's the
# script's job (it runs in CI under ``make weaver-conformance`` and
# fetches the schemas from raw.githubusercontent.com).  Locally,
# developers can populate ``.weaver-schemas/`` once and re-run the
# test suite to also exercise the schema-validation pass.
LOCAL_SCHEMAS = Path(__file__).resolve().parent.parent / ".weaver-schemas"


@pytest.mark.skipif(
    not LOCAL_SCHEMAS.is_dir(),
    reason="local weaver-spec schemas not present (.weaver-schemas/)",
)
def test_fixtures_validate_against_local_schemas() -> None:
    """When the schemas are available on disk, every fixture also
    validates against the spec JSON-Schema.

    Failures include the fixture file path via the script-level helper.
    """
    import sys

    sys.path.insert(0, str(LOCAL_SCHEMAS.parent))
    from scripts.weaver_spec_conformance import _check_fixture_files  # noqa: PLC0415

    _check_fixture_files(LOCAL_SCHEMAS, FIXTURE_DIR)

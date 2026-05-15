#!/usr/bin/env python3
"""weaver-spec conformance self-check for contextweaver.

Run via ``make weaver-conformance`` or directly:

    python scripts/weaver_spec_conformance.py
    python scripts/weaver_spec_conformance.py --schemas-dir .weaver-schemas

The script:

1. Constructs sample contextweaver objects (``SelectableItem``, ``ChoiceCard``,
   ``RoutingDecision``, ``ResultEnvelope``).
2. Maps each to the corresponding ``weaver_contracts`` type via
   :mod:`contextweaver.adapters.weaver_contracts` (the spec dataclasses run
   their own non-empty-field validation on construction).
3. Maps each spec object back and asserts equality with the input.
4. When ``--schemas-dir`` is supplied, additionally validates the JSON form of
   ``RoutingDecision``, ``ChoiceCard``, ``SelectableItem``, and ``Frame``
   against the schemas in that directory.

Exit codes: 0 on success, 1 on any failure.

Implements the acceptance criterion of issue #145 ("Verify that contextweaver's
RoutingDecision and ChoiceCard outputs validate against the JSON Schemas in
contracts/json/") — a stronger gate than the issue's stub-friendly default,
unlocked by treating ``weaver_contracts`` + ``jsonschema`` as ``[dev]`` deps.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from contextweaver.adapters.weaver_contracts import (  # noqa: E402
    from_weaver_choice_card_single,
    from_weaver_frame,
    from_weaver_routing_decision,
    from_weaver_selectable_item,
    to_weaver_choice_card,
    to_weaver_frame,
    to_weaver_routing_decision,
    to_weaver_selectable_item,
)
from contextweaver.envelope import ChoiceCard, ResultEnvelope, RoutingDecision  # noqa: E402
from contextweaver.types import ArtifactRef, SelectableItem, ViewSpec  # noqa: E402


# ---------------------------------------------------------------------------
# Sample fixtures
# ---------------------------------------------------------------------------


def _sample_selectable_item() -> SelectableItem:
    return SelectableItem(
        id="db.search",
        kind="tool",
        name="search",
        description="Search the customer database",
        tags=["db", "query"],
        namespace="db",
        args_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        side_effects=False,
        cost_hint=0.2,
        metadata={"owner": "platform"},
    )


def _sample_choice_card() -> ChoiceCard:
    return ChoiceCard(
        id="db.search",
        name="search",
        description="Search the customer database",
        tags=["db"],
        kind="tool",
        namespace="db",
        has_schema=True,
        cost_hint=0.2,
        side_effects=False,
        score=0.91,
    )


def _sample_routing_decision() -> RoutingDecision:
    cards = [
        ChoiceCard(id="db.search", name="search", description="Search", score=0.91),
        ChoiceCard(id="db.list", name="list", description="List records", score=0.62),
    ]
    return RoutingDecision(
        id="rd-conformance-1",
        choice_cards=cards,
        timestamp=datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc),
        selected_item_id="db.search",
        selected_card_id="db.search",
        context_summary="user asked to find customers",
        metadata={"trace_id": "abc-123"},
    )


def _sample_result_envelope() -> ResultEnvelope:
    return ResultEnvelope(
        status="partial",
        summary="3 of 5 rows returned",
        facts=["count: 3", "status: warning"],
        artifacts=[
            ArtifactRef(handle="h1", media_type="application/json", size_bytes=42, label="rows")
        ],
        views=[ViewSpec(view_id="head", label="first rows", selector={"start": 0, "end": 3})],
        provenance={"tool": "db.search", "redaction_notes": "PII masked"},
    )


# ---------------------------------------------------------------------------
# Round-trip checks
# ---------------------------------------------------------------------------


def _check_selectable_item_roundtrip() -> None:
    item = _sample_selectable_item()
    restored = from_weaver_selectable_item(to_weaver_selectable_item(item))
    if restored != item:
        raise AssertionError(f"SelectableItem round-trip drift: {item!r} != {restored!r}")


def _check_choice_card_roundtrip() -> None:
    card = _sample_choice_card()
    restored = from_weaver_choice_card_single(to_weaver_choice_card(card))
    if restored != card:
        raise AssertionError(f"ChoiceCard round-trip drift: {card!r} != {restored!r}")


def _check_routing_decision_roundtrip() -> None:
    decision = _sample_routing_decision()
    restored = from_weaver_routing_decision(to_weaver_routing_decision(decision))
    if restored != decision:
        raise AssertionError(
            f"RoutingDecision round-trip drift:\n  in:  {decision!r}\n  out: {restored!r}"
        )


def _check_frame_roundtrip() -> None:
    envelope = _sample_result_envelope()
    frame = to_weaver_frame(
        envelope,
        frame_id="f-conformance-1",
        capability_id="db:search",
        created_at=datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc),
    )
    restored = from_weaver_frame(frame)
    if restored != envelope:
        raise AssertionError(
            f"Frame round-trip drift:\n  in:  {envelope!r}\n  out: {restored!r}"
        )


# ---------------------------------------------------------------------------
# Optional JSON-Schema validation
# ---------------------------------------------------------------------------


def _spec_to_jsonable(obj: Any) -> Any:
    """Recursively convert weaver_contracts dataclasses into JSON-Schema-shaped dicts.

    Strips ``None`` values from dataclass output so that the resulting JSON
    matches each schema's "field absent" semantics — several spec fields
    (``context_hint``, ``redaction_notes``, ``context_summary``) are typed as
    plain ``string`` (not ``["string", "null"]``) so emitting ``null`` would
    fail validation.
    """
    if is_dataclass(obj) and not isinstance(obj, type):
        d = asdict(obj)
        return {k: _spec_to_jsonable(v) for k, v in d.items() if v is not None}
    if isinstance(obj, dict):
        return {k: _spec_to_jsonable(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_spec_to_jsonable(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def _validate_against_schema(payload: dict[str, Any], schema_path: Path) -> None:
    import jsonschema  # noqa: PLC0415  (optional dep, lazy)

    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    # Resolve sibling $ref schemas locally so the validator never hits the
    # network for the spec's internal cross-references.
    try:
        from referencing import Registry, Resource  # noqa: PLC0415
        from referencing.jsonschema import DRAFT202012  # noqa: PLC0415

        registry = Registry()
        for sibling in schema_path.parent.glob("*.schema.json"):
            sibling_doc = json.loads(sibling.read_text(encoding="utf-8"))
            sibling_id = sibling_doc.get("$id")
            if sibling_id:
                registry = registry.with_resource(
                    sibling_id, Resource.from_contents(sibling_doc, default_specification=DRAFT202012)
                )
        validator = jsonschema.Draft202012Validator(schema, registry=registry)
    except ImportError:  # pragma: no cover - referencing ships with jsonschema>=4.18
        validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda e: e.path)
    if errors:
        msgs = "\n  - ".join(f"{list(e.path)}: {e.message}" for e in errors)
        raise AssertionError(f"JSON-Schema validation failed for {schema_path.name}:\n  - {msgs}")


def _check_schemas(schemas_dir: Path) -> None:
    """Validate adapter-produced spec payloads against weaver-spec JSON Schemas.

    Note: only the adapter's ``to_weaver_*`` output is validated. The
    contextweaver-side :meth:`RoutingDecision.to_dict` produces a
    *contextweaver-shaped* document (1:1 cards in ``choice_cards``), not a
    spec-shaped one — see ``docs/weaver_spec_mapping.md``. The corresponding
    contract claim in the docstring of
    :class:`contextweaver.envelope.RoutingDecision` directs callers through
    ``to_weaver_routing_decision()`` when they need schema-valid JSON, so the
    gate intentionally does not run the spec schema against
    ``to_dict()`` output.
    """
    # SelectableItem
    spec_item = to_weaver_selectable_item(_sample_selectable_item())
    _validate_against_schema(
        _spec_to_jsonable(spec_item), schemas_dir / "selectable_item.schema.json"
    )
    # ChoiceCard
    spec_card = to_weaver_choice_card(_sample_choice_card())
    _validate_against_schema(_spec_to_jsonable(spec_card), schemas_dir / "choice_card.schema.json")
    # RoutingDecision
    spec_decision = to_weaver_routing_decision(_sample_routing_decision())
    _validate_against_schema(
        _spec_to_jsonable(spec_decision), schemas_dir / "routing_decision.schema.json"
    )
    # Frame
    spec_frame = to_weaver_frame(
        _sample_result_envelope(),
        frame_id="f-conformance-1",
        capability_id="db:search",
        created_at=datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc),
    )
    _validate_against_schema(_spec_to_jsonable(spec_frame), schemas_dir / "frame.schema.json")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="weaver_spec_conformance",
        description="Run round-trip and JSON-Schema conformance checks against weaver-spec.",
    )
    parser.add_argument(
        "--schemas-dir",
        type=Path,
        default=None,
        help=(
            "Optional directory containing weaver-spec JSON schemas "
            "(routing_decision.schema.json, etc.).  When omitted, only "
            "round-trip checks run."
        ),
    )
    args = parser.parse_args(argv)

    checks: list[tuple[str, Any]] = [
        ("SelectableItem round-trip", _check_selectable_item_roundtrip),
        ("ChoiceCard round-trip", _check_choice_card_roundtrip),
        ("RoutingDecision round-trip", _check_routing_decision_roundtrip),
        ("Frame round-trip", _check_frame_roundtrip),
    ]
    if args.schemas_dir is not None:
        schemas_dir: Path = args.schemas_dir
        if not schemas_dir.is_dir():
            print(f"error: --schemas-dir does not exist: {schemas_dir}", file=sys.stderr)
            return 1
        checks.append(("JSON-Schema validation", lambda: _check_schemas(schemas_dir)))

    failed = 0
    for name, fn in checks:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - reported to user
            print(f"FAIL: {name}\n  {exc}", file=sys.stderr)
            failed += 1
        else:
            print(f"ok:   {name}")
    if failed:
        print(f"\n{failed} check(s) failed", file=sys.stderr)
        return 1
    print(f"\nAll {len(checks)} weaver-spec conformance check(s) passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

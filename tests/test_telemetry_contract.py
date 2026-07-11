"""Tests for the telemetry handoff contract (issue #382)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from contextweaver.diagnostics import DiagnosticEvent
from contextweaver.exceptions import ConfigError
from contextweaver.telemetry_contract import (
    EVENT_FAMILIES,
    MAX_ATTRIBUTE_CHARS,
    RESERVED_FAMILIES,
    TELEMETRY_CONTRACT_VERSION,
    TELEMETRY_SCHEMA_ID,
    classify_event,
    export_jsonl,
    read_jsonl,
    validate_event_dict,
)

FIXTURE = Path(__file__).parent / "fixtures" / "telemetry_v1_sample.jsonl"
SCHEMA = Path(__file__).parent.parent / "schemas" / "telemetry" / "v1"
SCHEMA_FILE = SCHEMA / "diagnostic_event.schema.json"


def _fixture_dicts() -> list[dict[str, Any]]:
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _make_event(name: str = "execute.completed") -> DiagnosticEvent:
    return DiagnosticEvent(
        event=name,
        timestamp="2026-07-11T09:15:00+00:00",
        session_id="s1",
        duration_ms=1.5,
        tool_id="github.create_issue",
        namespace="github",
        attributes={"arg_keys": ["title"], "status": "ok"},
    )


def test_contract_version() -> None:
    assert TELEMETRY_CONTRACT_VERSION == 1


def test_fixture_events_validate_with_zero_problems() -> None:
    dicts = _fixture_dicts()
    assert len(dicts) >= 12
    for data in dicts:
        assert validate_event_dict(data) == []


def test_fixture_events_all_classify_and_cover_six_families() -> None:
    events, problems = read_jsonl(FIXTURE)
    assert problems == []
    families = {classify_event(event) for event in events}
    assert None not in families
    assert len(families) >= 6


def test_event_families_mapping_shape() -> None:
    expected = {
        "catalog_inventory",
        "route_request",
        "shortlist",
        "schema_hydration",
        "execution",
        "firewall_artifact",
        "policy_denial",
        "visibility",
    }
    assert set(EVENT_FAMILIES) == expected
    assert RESERVED_FAMILIES.issubset(expected)
    with pytest.raises(TypeError):
        EVENT_FAMILIES["execution"] = ("x.",)  # type: ignore[index]


def test_classify_event_prefix_match_and_unknown() -> None:
    assert classify_event(_make_event("catalog.loaded")) == "catalog_inventory"
    assert classify_event(_make_event("browse.completed")) == "route_request"
    assert classify_event(_make_event("hydrate.failed")) == "schema_hydration"
    assert classify_event(_make_event("execute.dry_run")) == "execution"
    assert classify_event(_make_event("view.completed")) == "firewall_artifact"
    assert classify_event(_make_event("visibility.denied")) == "visibility"
    assert classify_event(_make_event("policy.denied")) == "policy_denial"
    assert classify_event(_make_event("shortlist.composed")) == "shortlist"
    assert classify_event(_make_event("unknown.event")) is None
    assert classify_event(_make_event("executes")) is None  # prefix needs the dot


def test_validate_event_dict_missing_and_wrong_types() -> None:
    problems = validate_event_dict({"event": "execute.completed"})
    assert any("missing required key: version" in p for p in problems)
    assert any("missing required key: timestamp" in p for p in problems)
    assert any("missing required key: success" in p for p in problems)
    assert any("missing required key: session_id" in p for p in problems)

    bad = _make_event().to_dict()
    bad["success"] = "yes"
    bad["duration_ms"] = "fast"
    bad["attributes"] = ["nope"]
    problems = validate_event_dict(bad)
    assert any("success must be a boolean" in p for p in problems)
    assert any("duration_ms must be a number" in p for p in problems)
    assert any("attributes must be an object" in p for p in problems)

    empty_name = _make_event().to_dict()
    empty_name["event"] = ""
    assert any("non-empty" in p for p in validate_event_dict(empty_name))


def test_validate_event_dict_accepts_nulls_where_allowed() -> None:
    data = _make_event().to_dict()
    data["duration_ms"] = None
    data["tool_id"] = None
    data["namespace"] = None
    assert validate_event_dict(data) == []


def test_payload_leak_heuristic_fires_on_long_attribute() -> None:
    data = _make_event().to_dict()
    data["attributes"] = {"blob": "x" * 3000}
    problems = validate_event_dict(data)
    assert len(problems) == 1
    assert "likely payload leakage" in problems[0]
    assert "blob" in problems[0]

    data["attributes"] = {"ok": "x" * MAX_ATTRIBUTE_CHARS}
    assert validate_event_dict(data) == []


def test_jsonl_round_trip(tmp_path: Path) -> None:
    events = [_make_event("browse.completed"), _make_event("execute.completed")]
    path = tmp_path / "events.jsonl"
    assert export_jsonl(events, path) == 2

    loaded, problems = read_jsonl(path)
    assert problems == []
    assert [e.to_dict() for e in loaded] == [e.to_dict() for e in events]


def test_read_jsonl_collects_malformed_lines_without_raising(tmp_path: Path) -> None:
    good = json.dumps(_make_event().to_dict(), sort_keys=True)
    path = tmp_path / "mixed.jsonl"
    path.write_text(
        good + "\n" + "{not json\n" + '["array", "line"]\n' + '{"event": "x.y"}\n' + "\n",
        encoding="utf-8",
    )

    events, problems = read_jsonl(path)

    assert len(events) == 1
    assert events[0].event == "execute.completed"
    assert len(problems) >= 3
    assert any("invalid JSON" in p for p in problems)
    assert any("must be a JSON object" in p for p in problems)
    assert any("missing required key" in p for p in problems)


def test_read_jsonl_keeps_leaky_event_but_reports_it(tmp_path: Path) -> None:
    data = _make_event().to_dict()
    data["attributes"] = {"blob": "x" * 3000}
    path = tmp_path / "leaky.jsonl"
    path.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")

    events, problems = read_jsonl(path)

    assert len(events) == 1
    assert len(problems) == 1
    assert "likely payload leakage" in problems[0]


def test_read_jsonl_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        read_jsonl(tmp_path / "absent.jsonl")


def test_published_schema_is_valid_and_accepts_fixture() -> None:
    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert schema["$id"] == TELEMETRY_SCHEMA_ID
    assert set(schema["required"]) == {"version", "event", "timestamp", "success", "session_id"}
    assert schema["properties"]["attributes"]["additionalProperties"] is True

    validator = Draft202012Validator(schema)
    for data in _fixture_dicts():
        validator.validate(data)


def test_published_schema_rejects_missing_required_field() -> None:
    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    data = _make_event().to_dict()
    del data["session_id"]
    assert not validator.is_valid(data)

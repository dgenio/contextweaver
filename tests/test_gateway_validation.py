"""Tests for contextweaver.adapters.gateway_validation (#464, #484).

Covers the pure schema-health and catalog-refresh-report helpers that harden
the gateway ingest path against untrusted upstream tool definitions.
"""

from __future__ import annotations

import jsonschema.exceptions
import pytest

from contextweaver.adapters.gateway_validation import (
    CatalogRefreshReport,
    SchemaFinding,
    SchemaLimits,
    SkippedTool,
    build_validator,
    check_schema_health,
)

# ---------------------------------------------------------------------------
# check_schema_health — well-formedness
# ---------------------------------------------------------------------------


def test_well_formed_schema_has_no_findings() -> None:
    schema = {
        "type": "object",
        "properties": {"title": {"type": "string"}},
        "required": ["title"],
    }
    assert check_schema_health("acme:deploy#deadbeef", schema) == []


@pytest.mark.parametrize("schema", [None, {}])
def test_empty_schema_is_always_healthy(schema: dict[str, object] | None) -> None:
    # The sentinel "no schema" / "{}" cases impose no validation.
    assert check_schema_health("acme:noop#00000000", schema) == []


def test_malformed_schema_reports_not_well_formed() -> None:
    # "type": "strnig" is not a valid JSON Schema type keyword value.
    findings = check_schema_health("acme:query#00000000", {"type": "strnig"})
    assert len(findings) == 1
    assert findings[0].kind == "not_well_formed"
    assert findings[0].tool_id == "acme:query#00000000"


# ---------------------------------------------------------------------------
# check_schema_health — complexity bounds
# ---------------------------------------------------------------------------


def test_depth_bound_flagged() -> None:
    # Nest objects one level past a limit of 2: properties->a->properties->b.
    schema: dict[str, object] = {"type": "object"}
    node = schema
    for _ in range(4):
        child: dict[str, object] = {"type": "object"}
        node["properties"] = {"x": child}
        node = child
    findings = check_schema_health("acme:deep#00000000", schema, limits=SchemaLimits(max_depth=2))
    kinds = {f.kind for f in findings}
    assert "depth_exceeded" in kinds


def test_size_bound_flagged() -> None:
    big = {"type": "object", "description": "x" * 5000}
    findings = check_schema_health("acme:big#00000000", big, limits=SchemaLimits(max_bytes=512))
    assert any(f.kind == "size_exceeded" for f in findings)


def test_property_count_bound_flagged() -> None:
    schema = {"type": "object", "properties": {f"p{i}": {"type": "string"} for i in range(10)}}
    findings = check_schema_health(
        "acme:wide#00000000", schema, limits=SchemaLimits(max_properties=3)
    )
    assert any(f.kind == "properties_exceeded" for f in findings)


def test_generous_defaults_pass_a_realistic_schema() -> None:
    schema = {
        "type": "object",
        "properties": {f"field_{i}": {"type": "string"} for i in range(50)},
    }
    # 50 properties is well within the default 512 cap.
    assert check_schema_health("acme:realistic#00000000", schema) == []


# ---------------------------------------------------------------------------
# build_validator + caching contract
# ---------------------------------------------------------------------------


def test_build_validator_validates_against_schema() -> None:
    validator = build_validator(
        {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    )
    validator.validate({"n": 5})  # ok
    with pytest.raises(jsonschema.exceptions.ValidationError):
        validator.validate({})  # missing required


def test_build_validator_rejects_malformed_schema() -> None:
    with pytest.raises(jsonschema.exceptions.SchemaError):
        build_validator({"type": "strnig"})


# ---------------------------------------------------------------------------
# Report dataclasses
# ---------------------------------------------------------------------------


def test_catalog_refresh_report_int_and_ok() -> None:
    report = CatalogRefreshReport(registered=3)
    assert int(report) == 3
    assert report.ok is True

    report.skipped.append(SkippedTool(index=1, name="bad", reason="no name"))
    assert report.ok is False


def test_report_to_dict_round_trips_findings() -> None:
    report = CatalogRefreshReport(
        registered=1,
        skipped=[SkippedTool(index=0, name="", reason="not a dict")],
        schema_findings=[SchemaFinding("acme:x#00000000", "depth_exceeded", "depth 41 > 32")],
    )
    payload = report.to_dict()
    assert payload["registered"] == 1
    assert payload["skipped"][0] == {"index": 0, "name": "", "reason": "not a dict"}
    assert payload["schema_findings"][0]["kind"] == "depth_exceeded"

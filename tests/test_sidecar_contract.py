"""Tests for the sidecar wire contract + published v1 schemas (issue #674).

Two responsibilities:

1. Every request/response/error dataclass round-trips through
   ``to_dict`` / ``from_dict`` and rejects malformed input with a typed
   :class:`~contextweaver.exceptions.ConfigError`.
2. The committed example payloads under ``schemas/sidecar/v1/examples/``
   validate against the published JSON Schemas — i.e. the contract is honest.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from contextweaver.adapters.sidecar_contract import (
    SIDECAR_API_VERSION,
    CompactRequest,
    CompactResponse,
    RouteRequest,
    RouteResponse,
    SidecarError,
)
from contextweaver.exceptions import ConfigError

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = REPO_ROOT / "schemas" / "sidecar" / "v1"


def _load(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


# --- RouteRequest ------------------------------------------------------------


def test_route_request_round_trip() -> None:
    req = RouteRequest(query="send email", top_k=5, allowed_namespaces=["email"])
    assert RouteRequest.from_dict(req.to_dict()) == req


def test_route_request_defaults() -> None:
    req = RouteRequest.from_dict({"query": "hi"})
    assert req.top_k == 10
    assert req.exclude_ids == []


@pytest.mark.parametrize(
    "payload",
    [
        {},  # missing query
        {"query": ""},  # blank query
        {"query": "x", "top_k": 0},  # top_k below 1
        {"query": "x", "top_k": "many"},  # wrong type
        {"query": "x", "exclude_ids": [1, 2]},  # non-string list
    ],
)
def test_route_request_rejects_bad_input(payload: dict) -> None:
    with pytest.raises(ConfigError):
        RouteRequest.from_dict(payload)


# --- CompactRequest ----------------------------------------------------------


def test_compact_request_round_trip() -> None:
    req = CompactRequest(data={"a": 1}, threshold_chars=10, strategy="structured", keep=["a"])
    assert CompactRequest.from_dict(req.to_dict()) == req


@pytest.mark.parametrize(
    "payload",
    [
        {},  # missing data
        {"data": 123},  # data not object/array/string
        {"data": "x", "strategy": "bogus"},  # invalid strategy
        {"data": "x", "threshold_chars": -1},  # negative threshold
    ],
)
def test_compact_request_rejects_bad_input(payload: dict) -> None:
    with pytest.raises(ConfigError):
        CompactRequest.from_dict(payload)


# --- Responses + error -------------------------------------------------------


def test_route_response_carries_api_version() -> None:
    resp = RouteResponse(candidate_ids=["a"], scores=[1.0])
    assert resp.to_dict()["api_version"] == SIDECAR_API_VERSION


def test_compact_response_serialises() -> None:
    resp = CompactResponse(firewalled=False, payload={"x": 1}, tokens_saved=3)
    out = resp.to_dict()
    assert out["api_version"] == SIDECAR_API_VERSION
    assert out["tokens_saved"] == 3


def test_sidecar_error_round_trip() -> None:
    err = SidecarError(code="RATE_LIMITED", message="slow down", retryable=True, details={"s": 1})
    restored = SidecarError.from_dict(err.to_dict())
    assert restored == err
    assert err.to_dict()["error"] == "RATE_LIMITED"


# --- Published schemas validate the examples --------------------------------


@pytest.mark.parametrize(
    ("schema_file", "example_file"),
    [
        ("route_request.schema.json", "route_request.json"),
        ("route_response.schema.json", "route_response.json"),
        ("compact_request.schema.json", "compact_request.json"),
        ("compact_response.schema.json", "compact_response.json"),
        ("error.schema.json", "error.json"),
    ],
)
def test_examples_validate_against_schemas(schema_file: str, example_file: str) -> None:
    schema = _load(schema_file)
    example = json.loads((SCHEMA_DIR / "examples" / example_file).read_text(encoding="utf-8"))
    jsonschema.validate(example, schema)


def test_live_payloads_validate_against_schemas() -> None:
    # The dataclasses' own ``to_dict`` output must satisfy the published schema.
    jsonschema.validate(RouteRequest(query="q").to_dict(), _load("route_request.schema.json"))
    jsonschema.validate(
        RouteResponse(candidate_ids=["a"], scores=[0.5]).to_dict(),
        _load("route_response.schema.json"),
    )
    jsonschema.validate(
        CompactResponse(firewalled=True, payload={"_cw": {}}, tokens_saved=5).to_dict(),
        _load("compact_response.schema.json"),
    )

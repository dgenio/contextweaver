"""Tests for the structured (lossless) firewall projection (issue #406)."""

from __future__ import annotations

import json

import pytest

from contextweaver.exceptions import ConfigError
from contextweaver.summarize.structured import StructuredFirewall, parse_path, project

_INVOICES = {
    "result": {
        "response": {
            "invoices": [
                {"invoiceNumber": "A-1", "amount": 100, "status": "paid", "notes": "x" * 500},
                {"invoiceNumber": "A-2", "amount": 200, "status": "due", "notes": "y" * 500},
            ],
            "total": 300,
            "secret": "do-not-leak",
        }
    }
}


def test_parse_path_splits_keys_and_list_marker() -> None:
    assert parse_path("result.response.invoices[].amount") == [
        "result",
        "response",
        "invoices",
        "[]",
        "amount",
    ]


def test_parse_path_rejects_empty() -> None:
    with pytest.raises(ConfigError):
        parse_path("   ")


def test_project_keeps_only_allow_listed_paths() -> None:
    projected = project(
        _INVOICES,
        [
            "result.response.invoices[].invoiceNumber",
            "result.response.invoices[].amount",
            "result.response.invoices[].status",
            "result.response.total",
        ],
    )
    assert projected == {
        "result": {
            "response": {
                "invoices": [
                    {"invoiceNumber": "A-1", "amount": 100, "status": "paid"},
                    {"invoiceNumber": "A-2", "amount": 200, "status": "due"},
                ],
                "total": 300,
            }
        }
    }
    # The bulky `notes` and the sensitive `secret` are dropped from the inline.
    assert "do-not-leak" not in json.dumps(projected)
    assert "notes" not in json.dumps(projected)


def test_project_skips_unresolved_paths() -> None:
    assert project({"a": 1}, ["b.c", "a"]) == {"a": 1}


def test_project_no_matches_returns_empty_dict() -> None:
    assert project({"a": 1}, ["x.y.z"]) == {}


def test_project_is_deterministic() -> None:
    keep = ["result.response.invoices[].amount", "result.response.total"]
    assert json.dumps(project(_INVOICES, keep), sort_keys=True) == json.dumps(
        project(_INVOICES, keep), sort_keys=True
    )


def test_structured_firewall_requires_non_empty_keep() -> None:
    with pytest.raises(ConfigError):
        StructuredFirewall(keep=[])


def test_structured_firewall_validates_paths_eagerly() -> None:
    with pytest.raises(ConfigError):
        StructuredFirewall(keep=[""])


def test_structured_firewall_compact_returns_projection_and_facts() -> None:
    fw = StructuredFirewall(keep=["result.response.total"])
    projected, facts = fw.compact(_INVOICES)
    assert projected == {"result": {"response": {"total": 300}}}
    assert isinstance(facts, list)

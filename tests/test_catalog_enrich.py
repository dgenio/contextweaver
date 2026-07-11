"""Tests for LLM-assisted catalog enrichment (issue #383)."""

from __future__ import annotations

import json

import pytest

from contextweaver.exceptions import ConfigError
from contextweaver.extras.catalog_enrich import (
    EnrichmentReport,
    EnrichmentSuggestion,
    apply_suggestions,
    enrich_catalog,
)
from contextweaver.extras.llm_guard import GuardPolicy
from contextweaver.routing.catalog_metadata import inventory_of
from contextweaver.types import SelectableItem


def _item(
    item_id: str = "fs::read", name: str = "read", description: str = "reads"
) -> SelectableItem:
    return SelectableItem(
        id=item_id,
        kind="tool",
        name=name,
        description=description,
        namespace="fs",
        args_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    )


def test_valid_response_becomes_suggestions() -> None:
    def call_fn(prompt: str) -> str:
        assert "read" in prompt and "path" in prompt  # metadata-only prompt
        return json.dumps(
            {
                "description": "Read a file from the workspace filesystem.",
                "risk_level": "low",
                "aliases": ["open_file", "cat"],
                "rationale": "clearer verb-object phrasing",
            }
        )

    report = enrich_catalog([_item()], call_fn, provider_metadata={"model": "m1"})
    fields = {s.field for s in report.suggestions}
    assert fields == {"description", "risk_level", "aliases"}
    assert report.skipped == []
    assert report.provider_metadata == {"model": "m1"}
    description = next(s for s in report.suggestions if s.field == "description")
    assert description.current == "reads"
    assert description.source == "llm_assisted"


def test_input_items_never_mutated() -> None:
    item = _item()
    before = item.to_dict()

    def call_fn(prompt: str) -> str:
        return json.dumps({"description": "changed"})

    enrich_catalog([item], call_fn)
    assert item.to_dict() == before


def test_invalid_enum_and_malformed_json_are_skipped() -> None:
    responses = iter(
        [
            json.dumps({"risk_level": "catastrophic"}),
            "not json at all",
        ]
    )

    def call_fn(prompt: str) -> str:
        return next(responses)

    report = enrich_catalog([_item("a::t1", "t1"), _item("b::t2", "t2")], call_fn)
    assert report.suggestions == []
    reasons = dict(report.skipped)
    assert "invalid value" in reasons["a::t1"]
    assert "malformed response" in reasons["b::t2"]


def test_guard_rejection_becomes_skip_reason() -> None:
    def call_fn(prompt: str) -> str:
        return json.dumps({"description": "fine"})

    report = enrich_catalog(
        [_item("a::t1", "t1"), _item("b::t2", "t2")],
        call_fn,
        guard_policy=GuardPolicy(max_calls=1),
    )
    assert len(report.suggestions) == 1
    ((tool_id, reason),) = report.skipped
    assert tool_id == "b::t2" and "call failed" in reason


def test_unknown_field_request_rejected() -> None:
    with pytest.raises(ConfigError):
        enrich_catalog([_item()], lambda p: "{}", fields=("description", "owner_team"))


def test_apply_only_accepted_pairs() -> None:
    def call_fn(prompt: str) -> str:
        return json.dumps({"description": "Better.", "risk_level": "low"})

    items = [_item()]
    report = enrich_catalog(items, call_fn)
    applied = apply_suggestions(items, report, accept={("fs::read", "description")})
    assert applied[0].description == "Better."
    assert inventory_of(applied[0]) is None  # risk_level suggestion not accepted
    assert items[0].description == "reads"  # originals untouched

    both = apply_suggestions(
        items, report, accept={("fs::read", "description"), ("fs::read", "risk_level")}
    )
    inventory = inventory_of(both[0])
    assert inventory is not None and inventory.risk_level == "low"


def test_apply_refuses_advisory_fields_and_unknown_pairs() -> None:
    report = EnrichmentReport(
        suggestions=[EnrichmentSuggestion(tool_id="fs::read", field="aliases", suggested=["x"])]
    )
    with pytest.raises(ConfigError):
        apply_suggestions([_item()], report, accept={("fs::read", "aliases")})
    with pytest.raises(ConfigError):
        apply_suggestions([_item()], report, accept={("fs::read", "description")})


def test_renders_and_serde() -> None:
    def call_fn(prompt: str) -> str:
        return json.dumps({"description": "With | pipe."})

    report = enrich_catalog([_item()], call_fn, provider_metadata={"model": "m1"})
    markdown = report.render_markdown()
    assert "llm_assisted" in markdown and "\\|" in markdown
    jsonl = report.render_jsonl()
    restored = EnrichmentSuggestion.from_dict(json.loads(jsonl.splitlines()[0]))
    assert restored.field == "description"
    assert EnrichmentReport().to_dict()["suggestions"] == []
    with pytest.raises(ConfigError):
        EnrichmentSuggestion.from_dict({"tool_id": "t", "field": "nope", "suggested": "x"})


def test_max_items_caps_the_run() -> None:
    calls = {"n": 0}

    def call_fn(prompt: str) -> str:
        calls["n"] += 1
        return json.dumps({"description": "d"})

    enrich_catalog([_item(f"n::{i}", f"t{i}") for i in range(5)], call_fn, max_items=2)
    assert calls["n"] == 2

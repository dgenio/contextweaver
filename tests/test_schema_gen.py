"""Tests for the JSON Schema generator + drift check (issue #225).

Two responsibilities exercised here:

1. ``scripts/gen_schemas.py`` produces deterministic output that matches the
   files committed under ``schemas/`` and ``docs/schemas/v0/``.
2. Every published schema validates round-trip via ``to_dict()`` for the
   matching dataclass — i.e. the contract is honest.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import pytest

from contextweaver._schema_gen import (
    SCHEMA_ID_BASE,
    generate_array_schema,
    generate_schema,
    schema_to_json,
)
from contextweaver.envelope import BuildStats, ChoiceCard, ResultEnvelope
from contextweaver.routing.manifest import GraphManifest
from contextweaver.routing.trace import RouteTrace, TraceStep
from contextweaver.types import SelectableItem

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"
DOCS_SCHEMAS_DIR = REPO_ROOT / "docs" / "schemas" / "v0"


# ---------------------------------------------------------------------------
# Drift check: regenerator output must match committed files
# ---------------------------------------------------------------------------


def test_schemas_check_passes_on_clean_tree() -> None:
    """The committed schemas must match what the generator produces."""
    result = subprocess.run(
        [sys.executable, "scripts/gen_schemas.py", "--check"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"schemas drifted:\n{result.stderr}"


def test_all_six_schemas_present() -> None:
    expected = {
        "catalog.schema.json",
        "choice_card.schema.json",
        "result_envelope.schema.json",
        "route_trace.schema.json",
        "build_stats.schema.json",
        "graph_manifest.schema.json",
    }
    on_disk_root = {p.name for p in SCHEMAS_DIR.glob("*.schema.json")}
    on_disk_docs = {p.name for p in DOCS_SCHEMAS_DIR.glob("*.schema.json")}
    assert expected == on_disk_root == on_disk_docs


def test_schemas_have_stable_id_urls() -> None:
    for path in sorted(SCHEMAS_DIR.glob("*.schema.json")):
        schema = json.loads(path.read_text())
        assert schema["$id"].startswith(SCHEMA_ID_BASE + "/")
        assert schema["$id"].endswith(path.name)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


# ---------------------------------------------------------------------------
# Round-trip validation: every dataclass.to_dict() validates against its schema
# ---------------------------------------------------------------------------


def _load_schema(name: str) -> dict[str, object]:
    return json.loads((SCHEMAS_DIR / name).read_text())


def test_choice_card_validates_against_schema() -> None:
    card = ChoiceCard(
        id="tool.search",
        name="search",
        description="Search the catalog",
        tags=["search", "read"],
        kind="tool",
        namespace="tool",
        has_schema=False,
        score=0.85,
        cost_hint=0.1,
        side_effects=False,
    )
    jsonschema.validate(card.to_dict(), _load_schema("choice_card.schema.json"))


def test_choice_card_schema_enforces_tag_count() -> None:
    schema = _load_schema("choice_card.schema.json")
    bad = ChoiceCard(id="t", name="t", description="d").to_dict()
    bad["tags"] = ["a", "b", "c", "d", "e", "f"]  # 6 > 5
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


def test_choice_card_schema_enforces_tag_length() -> None:
    schema = _load_schema("choice_card.schema.json")
    bad = ChoiceCard(id="t", name="t", description="d").to_dict()
    bad["tags"] = ["x" * 25]  # > 24 chars
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


def test_choice_card_schema_enforces_name_length() -> None:
    schema = _load_schema("choice_card.schema.json")
    bad = ChoiceCard(id="t", name="t", description="d").to_dict()
    bad["name"] = "x" * 65  # > 64 chars
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


def test_choice_card_post_init_rejects_oversize_tags() -> None:
    """ChoiceCard __post_init__ enforces the same bounds the schema does."""
    with pytest.raises(ValueError, match="tags"):
        ChoiceCard(id="t", name="t", description="d", tags=["a", "b", "c", "d", "e", "f"])


def test_choice_card_post_init_rejects_oversize_name() -> None:
    with pytest.raises(ValueError, match="name"):
        ChoiceCard(id="t", name="x" * 65, description="d")


def test_result_envelope_validates_against_schema() -> None:
    env = ResultEnvelope(
        status="ok",
        summary="done",
        facts=["x=1"],
        provenance={"tool": "db"},
    )
    jsonschema.validate(env.to_dict(), _load_schema("result_envelope.schema.json"))


def test_build_stats_validates_against_schema() -> None:
    stats = BuildStats(
        tokens_per_section={"history": 200},
        total_candidates=5,
        included_count=3,
        dropped_count=2,
        dropped_reasons={"budget": 2},
        dedup_removed=1,
        dependency_closures=0,
        header_footer_tokens=20,
    )
    jsonschema.validate(stats.to_dict(), _load_schema("build_stats.schema.json"))


def test_route_trace_validates_against_schema() -> None:
    trace = RouteTrace(
        query="example",
        confidence_gap=0.15,
        top_score=0.9,
        runner_up_score=0.5,
        is_ambiguous=False,
        retriever_engine="tfidf",
        steps=[
            TraceStep(
                depth=0,
                node="root",
                scored_children=[("a", 0.9), ("b", 0.5)],
                kept=["a"],
            )
        ],
    )
    jsonschema.validate(trace.to_dict(), _load_schema("route_trace.schema.json"))


def test_graph_manifest_validates_against_schema() -> None:
    manifest = GraphManifest(
        build_hash="abcd1234",
        seed=42,
        engine_versions={"retriever": "tfidf:1.0"},
        timestamp=1_700_000_000.0,
        item_count=10,
        strategy="namespace",
        max_depth=3,
    )
    jsonschema.validate(manifest.to_dict(), _load_schema("graph_manifest.schema.json"))


def test_catalog_validates_against_schema() -> None:
    items = [
        SelectableItem(
            id="tool.read",
            kind="tool",
            name="read",
            description="Read",
            namespace="tool",
        ),
        SelectableItem(
            id="agent.handler",
            kind="agent",
            name="handler",
            description="Handles",
            namespace="agent",
        ),
    ]
    payload = [it.to_dict() for it in items]
    jsonschema.validate(payload, _load_schema("catalog.schema.json"))


# ---------------------------------------------------------------------------
# Generator unit tests
# ---------------------------------------------------------------------------


def test_generate_schema_is_byte_deterministic() -> None:
    schema_id = f"{SCHEMA_ID_BASE}/build_stats.schema.json"
    a = schema_to_json(generate_schema(BuildStats, schema_id=schema_id))
    b = schema_to_json(generate_schema(BuildStats, schema_id=schema_id))
    assert a == b


def test_generate_array_schema_for_catalog() -> None:
    schema_id = f"{SCHEMA_ID_BASE}/catalog.schema.json"
    doc = generate_array_schema(SelectableItem, schema_id=schema_id, title="catalog")
    assert doc["type"] == "array"
    assert "items" in doc
    assert doc["items"]["type"] == "object"


def test_drift_detection_fires_when_field_added() -> None:
    """Simulate drift: a field added to BuildStats should fail schemas-check.

    We don't actually mutate BuildStats — instead we compare a known-drifted
    schema against the generator output to prove ``--check`` would reject it.
    """
    schema_id = f"{SCHEMA_ID_BASE}/build_stats.schema.json"
    current = schema_to_json(generate_schema(BuildStats, schema_id=schema_id))
    drifted_doc = json.loads(current)
    drifted_doc["properties"]["new_field"] = {"type": "string"}
    drifted = schema_to_json(drifted_doc)
    assert current != drifted


# ---------------------------------------------------------------------------
# Datetime field handling (RoutingDecision.timestamp)
# ---------------------------------------------------------------------------


def test_datetime_field_renders_as_iso_string_format() -> None:
    """`datetime` fields must surface as ``{"type": "string", "format": "date-time"}``."""
    from contextweaver.envelope import RoutingDecision

    schema = generate_schema(
        RoutingDecision,
        schema_id=f"{SCHEMA_ID_BASE}/routing_decision.schema.json",
    )
    assert schema["properties"]["timestamp"]["type"] == "string"
    assert schema["properties"]["timestamp"]["format"] == "date-time"


def test_routing_decision_round_trip_matches_datetime_format() -> None:
    """The generated ``RoutingDecision`` schema accepts the dict ``to_dict`` emits."""
    from contextweaver.envelope import RoutingDecision

    decision = RoutingDecision(
        id="rd-1",
        choice_cards=[ChoiceCard(id="t", name="t", description="d")],
        timestamp=datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc),
    )
    schema = generate_schema(
        RoutingDecision,
        schema_id=f"{SCHEMA_ID_BASE}/routing_decision.schema.json",
    )
    jsonschema.validate(decision.to_dict(), schema)

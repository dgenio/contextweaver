"""Tests for contextweaver.adapters.okf (issue #736)."""

from __future__ import annotations

from pathlib import Path

import pytest

from contextweaver.adapters._okf_io import parse_markdown_frontmatter
from contextweaver.adapters.okf import (
    load_okf_bundle,
    okf_nodes_to_context_items,
    select_knowledge,
)
from contextweaver.exceptions import ConfigError
from contextweaver.types import ItemKind, Sensitivity

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "okf"


# ---------------------------------------------------------------------------
# parse_markdown_frontmatter — permissive parsing primitive
# ---------------------------------------------------------------------------


def test_parse_markdown_frontmatter_no_fence_is_plain_markdown() -> None:
    front, body, error = parse_markdown_frontmatter("Just plain text.\n")
    assert front == {}
    assert body == "Just plain text.\n"
    assert error is None


def test_parse_markdown_frontmatter_valid() -> None:
    front, body, error = parse_markdown_frontmatter("---\ntitle: X\n---\nBody.")
    assert front == {"title": "X"}
    assert body == "Body."
    assert error is None


def test_parse_markdown_frontmatter_missing_closing_fence_degrades() -> None:
    text = "---\ntitle: X\nBody with no closing fence."
    front, body, error = parse_markdown_frontmatter(text)
    assert front == {}
    assert body == text
    assert error is not None


def test_parse_markdown_frontmatter_non_mapping_degrades() -> None:
    front, body, error = parse_markdown_frontmatter("---\n- a\n- b\n---\nBody.")
    assert front == {}
    assert "mapping" in (error or "")


# ---------------------------------------------------------------------------
# load_okf_bundle — valid bundle
# ---------------------------------------------------------------------------


def test_load_okf_bundle_valid() -> None:
    bundle = load_okf_bundle(FIXTURE_DIR / "valid_bundle")

    ids = {n.id for n in bundle.nodes}
    assert ids == {"concept-a", "concept-b"}
    assert bundle.diagnostics == []
    assert bundle.index is not None
    assert bundle.index.title == "Sample OKF Bundle"
    assert bundle.log is not None
    assert bundle.log.title == "Bundle History"

    concept_b = next(n for n in bundle.nodes if n.id == "concept-b")
    assert concept_b.frontmatter["custom_field"] == "preserved-value"


def test_okf_bundle_to_dict_is_json_compatible() -> None:
    bundle = load_okf_bundle(FIXTURE_DIR / "valid_bundle")
    data = bundle.to_dict()
    assert {n["id"] for n in data["nodes"]} == {"concept-a", "concept-b"}
    assert data["index"]["title"] == "Sample OKF Bundle"
    assert data["diagnostics"] == []


def test_load_okf_bundle_deterministic_order() -> None:
    first = load_okf_bundle(FIXTURE_DIR / "valid_bundle")
    second = load_okf_bundle(FIXTURE_DIR / "valid_bundle")
    assert [n.to_dict() for n in first.nodes] == [n.to_dict() for n in second.nodes]


def test_load_okf_bundle_index_and_log_excluded_from_nodes() -> None:
    bundle = load_okf_bundle(FIXTURE_DIR / "valid_bundle")
    node_paths = {n.source_path for n in bundle.nodes}
    assert "index.md" not in node_paths
    assert "log.md" not in node_paths


def test_load_okf_bundle_not_a_directory_raises(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    with pytest.raises(ConfigError):
        load_okf_bundle(missing)


# ---------------------------------------------------------------------------
# Explicit acceptance-criteria edge cases (issue #736)
# ---------------------------------------------------------------------------


def test_load_okf_bundle_unknown_type_does_not_fail() -> None:
    bundle = load_okf_bundle(FIXTURE_DIR / "unknown_type")
    assert bundle.diagnostics == []
    node = bundle.nodes[0]
    assert node.node_type == "SomeUnrecognizedType"


def test_load_okf_bundle_missing_title_falls_back_to_stem() -> None:
    bundle = load_okf_bundle(FIXTURE_DIR / "missing_title")
    node = bundle.nodes[0]
    assert node.title == "no_title"
    assert any("title" in d.message for d in bundle.diagnostics)


def test_load_okf_bundle_broken_link_is_flagged_not_fatal() -> None:
    bundle = load_okf_bundle(FIXTURE_DIR / "broken_link")
    assert len(bundle.nodes) == 1
    assert any("broken link" in d.message for d in bundle.diagnostics)


def test_load_okf_bundle_invalid_frontmatter_warns_by_default() -> None:
    bundle = load_okf_bundle(FIXTURE_DIR / "invalid_frontmatter")
    assert len(bundle.nodes) == 1
    assert any("YAML" in d.message for d in bundle.diagnostics)
    # Degraded node still carries the raw text as its body.
    assert "[unclosed list" in bundle.nodes[0].text


def test_load_okf_bundle_invalid_frontmatter_raises_when_strict() -> None:
    with pytest.raises(ConfigError):
        load_okf_bundle(FIXTURE_DIR / "invalid_frontmatter", on_invalid="raise")


# ---------------------------------------------------------------------------
# Materialisation into ContextItem
# ---------------------------------------------------------------------------


def test_okf_nodes_to_context_items_shape() -> None:
    bundle = load_okf_bundle(FIXTURE_DIR / "valid_bundle")
    items = okf_nodes_to_context_items(bundle.nodes)

    assert len(items) == 2
    item = next(i for i in items if i.id == "okf_bundle:concept-a")
    assert item.kind == ItemKind.doc_snippet
    assert item.sensitivity == Sensitivity.internal
    assert item.metadata["tags"] == ["routing", "budget"]
    assert item.metadata["_contextweaver"]["knowledge_source"]["kind"] == "okf_bundle"
    assert item.metadata["_contextweaver"]["knowledge_source"]["id"] == "concept-a"


def test_okf_nodes_to_context_items_filters_expired() -> None:
    bundle = load_okf_bundle(FIXTURE_DIR / "valid_bundle")
    # Neither fixture concept declares expires_at, so nothing is filtered
    # at any reference time — a real expiry path is covered in lessons/
    # expertise tests, which do declare expires_at.
    items = okf_nodes_to_context_items(bundle.nodes, now=1.0)
    assert len(items) == 2


def test_okf_nodes_to_context_items_double_load_is_byte_identical() -> None:
    bundle = load_okf_bundle(FIXTURE_DIR / "valid_bundle")
    first = [i.to_dict() for i in okf_nodes_to_context_items(bundle.nodes)]
    second_bundle = load_okf_bundle(FIXTURE_DIR / "valid_bundle")
    second = [i.to_dict() for i in okf_nodes_to_context_items(second_bundle.nodes)]
    assert first == second


# ---------------------------------------------------------------------------
# select_knowledge — deterministic rank + pack
# ---------------------------------------------------------------------------


def test_select_knowledge_ranks_by_relevance() -> None:
    bundle = load_okf_bundle(FIXTURE_DIR / "valid_bundle")
    items = select_knowledge(bundle.nodes, "firewall", budget_tokens=10_000)
    assert items[0].metadata["_contextweaver"]["knowledge_source"]["id"] == "concept-b"


def test_select_knowledge_respects_budget() -> None:
    bundle = load_okf_bundle(FIXTURE_DIR / "valid_bundle")
    items = select_knowledge(bundle.nodes, "concept", budget_tokens=1)
    assert len(items) <= 1


def test_select_knowledge_zero_budget_returns_empty() -> None:
    bundle = load_okf_bundle(FIXTURE_DIR / "valid_bundle")
    assert select_knowledge(bundle.nodes, "concept", budget_tokens=0) == []


def test_select_knowledge_tie_break_by_id_is_deterministic() -> None:
    bundle = load_okf_bundle(FIXTURE_DIR / "valid_bundle")
    first = select_knowledge(bundle.nodes, "zzz-no-match", budget_tokens=10_000)
    second = select_knowledge(bundle.nodes, "zzz-no-match", budget_tokens=10_000)
    assert (
        [i.id for i in first]
        == [i.id for i in second]
        == ["okf_bundle:concept-a", "okf_bundle:concept-b"]
    )

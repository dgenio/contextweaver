"""Tests for contextweaver.adapters.expertise_pack (issue #776)."""

from __future__ import annotations

from pathlib import Path

import pytest

from contextweaver.adapters.expertise_pack import (
    detect_conflicts,
    expertise_pack_to_context_items,
    load_expertise_pack,
)
from contextweaver.exceptions import ConfigError

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "expertise"

# expires_at in the stale fixture is 1_000_000_000; this is well past it.
LATER_NOW = 1_500_000_000.0


# ---------------------------------------------------------------------------
# Valid pack
# ---------------------------------------------------------------------------


def test_load_expertise_pack_valid() -> None:
    pack = load_expertise_pack(FIXTURE_DIR / "valid_pack")
    assert pack.version == "1.0"
    assert pack.diagnostics == []
    keys = {n.frontmatter["key"] for n in pack.nodes}
    assert keys == {"api-style", "verification-command"}

    data = pack.to_dict()
    assert data["version"] == "1.0"
    assert len(data["nodes"]) == 2


def test_load_expertise_pack_not_a_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_expertise_pack(tmp_path / "missing")


def test_expertise_pack_to_context_items_attributes_every_constraint() -> None:
    pack = load_expertise_pack(FIXTURE_DIR / "valid_pack")
    items = expertise_pack_to_context_items(pack)
    assert len(items) == 2
    for item in items:
        source = item.metadata["_contextweaver"]["knowledge_source"]
        assert source["kind"] == "expertise_pack"
        assert source["id"]


def test_expertise_pack_to_context_items_filters_by_task_tags() -> None:
    pack = load_expertise_pack(FIXTURE_DIR / "valid_pack")
    items = expertise_pack_to_context_items(pack, task_tags={"python-library"})
    assert len(items) == 2  # api-style declares applicable_to; verification has none (universal)

    items_unrelated = expertise_pack_to_context_items(pack, task_tags={"unrelated-domain"})
    ids = {i.metadata["_contextweaver"]["knowledge_source"]["id"] for i in items_unrelated}
    assert "constraint-api-style" not in ids  # applicable_to didn't match
    assert "constraint-verification" in ids  # no restriction declared -> always applies


# ---------------------------------------------------------------------------
# Acceptance-criteria edge cases: missing, malformed, stale, conflicting
# ---------------------------------------------------------------------------


def test_load_expertise_pack_missing_version(tmp_path: Path) -> None:
    """'missing' case: no index.md at all, so the version cannot be validated."""
    pack = load_expertise_pack(FIXTURE_DIR / "missing_version_pack")
    assert pack.version is None
    assert any("index.md" in d.message for d in pack.diagnostics)
    # The constraint node itself still loads.
    assert len(pack.nodes) == 1


def test_load_expertise_pack_missing_version_raises_when_strict() -> None:
    with pytest.raises(ConfigError):
        load_expertise_pack(FIXTURE_DIR / "missing_version_pack", on_invalid="raise")


def test_load_expertise_pack_malformed_missing_key() -> None:
    """'malformed' case: a constraint node with no 'key' field."""
    pack = load_expertise_pack(FIXTURE_DIR / "malformed_pack")
    assert pack.version == "1.0"
    assert any("key" in d.message for d in pack.diagnostics)


def test_load_expertise_pack_malformed_raises_when_strict() -> None:
    with pytest.raises(ConfigError):
        load_expertise_pack(FIXTURE_DIR / "malformed_pack", on_invalid="raise")


def test_expertise_pack_to_context_items_excludes_nodes_missing_key() -> None:
    """A node flagged 'not a valid constraint node' must never enter context."""
    pack = load_expertise_pack(FIXTURE_DIR / "malformed_pack")
    items = expertise_pack_to_context_items(pack)
    ids = {i.metadata["_contextweaver"]["knowledge_source"]["id"] for i in items}
    assert "constraint-no-key" not in ids
    assert ids == set()  # malformed_pack's only node lacks 'key'


def test_load_expertise_pack_stale_constraint_excluded_from_context() -> None:
    """'stale' case: an expired constraint is loaded but excluded at materialisation."""
    pack = load_expertise_pack(FIXTURE_DIR / "stale_pack")
    assert len(pack.nodes) == 1  # loading never drops expired nodes silently

    live_items = expertise_pack_to_context_items(pack, now=LATER_NOW)
    assert live_items == []

    not_yet_expired_items = expertise_pack_to_context_items(pack, now=1.0)
    assert len(not_yet_expired_items) == 1


def test_detect_conflicts_finds_contradicting_values() -> None:
    """'conflicting' case: two constraints share a key but disagree."""
    pack = load_expertise_pack(FIXTURE_DIR / "conflicting_pack")
    findings = detect_conflicts(pack.nodes)
    assert len(findings) == 1
    assert findings[0].key == "api-style"
    assert findings[0].node_ids == ("constraint-api-style-graphql", "constraint-api-style-rest")
    assert findings[0].to_dict() == {
        "key": "api-style",
        "node_ids": ["constraint-api-style-graphql", "constraint-api-style-rest"],
        "reason": findings[0].reason,
    }


def test_detect_conflicts_none_on_valid_pack() -> None:
    pack = load_expertise_pack(FIXTURE_DIR / "valid_pack")
    assert detect_conflicts(pack.nodes) == []


def test_detect_conflicts_ignores_expired_constraints() -> None:
    pack = load_expertise_pack(FIXTURE_DIR / "stale_pack")
    assert detect_conflicts(pack.nodes, now=LATER_NOW) == []


def test_detect_conflicts_respects_task_tag_applicability(tmp_path: Path) -> None:
    """Two constraints under the same key don't conflict if they never co-apply."""
    (tmp_path / "index.md").write_text('---\nversion: "1.0"\n---\nx', encoding="utf-8")
    (tmp_path / "a.md").write_text(
        "---\nid: a\nkey: k\napplicable_to: [scope-a]\n---\nValue A", encoding="utf-8"
    )
    (tmp_path / "b.md").write_text(
        "---\nid: b\nkey: k\napplicable_to: [scope-b]\n---\nValue B", encoding="utf-8"
    )
    pack = load_expertise_pack(tmp_path)

    assert detect_conflicts(pack.nodes) != []  # no task context -> compare everything
    assert detect_conflicts(pack.nodes, task_tags={"scope-a"}) == []  # only "a" applies


# ---------------------------------------------------------------------------
# Non-UTF-8 content must degrade, never raise (review fix)
# ---------------------------------------------------------------------------


def test_load_expertise_pack_non_utf8_constraint_file_does_not_raise(tmp_path: Path) -> None:
    (tmp_path / "index.md").write_text('---\nversion: "1.0"\n---\nx', encoding="utf-8")
    (tmp_path / "bad.md").write_bytes(b"---\nid: bad-enc\nkey: k\n---\n\xff\xfe")
    pack = load_expertise_pack(tmp_path)
    assert len(pack.nodes) == 1
    assert "�" in pack.nodes[0].text


def test_load_expertise_pack_non_utf8_index_md_does_not_raise(tmp_path: Path) -> None:
    (tmp_path / "index.md").write_bytes(b'---\nversion: "1.0"\n---\n\xff\xfe')
    pack = load_expertise_pack(tmp_path)
    assert pack.version == "1.0"

"""Tests for contextweaver.adapters.agent_skills (issue #545)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from contextweaver.adapters.agent_skills import (
    SkillBodySource,
    load_skills_catalog,
    parse_skill_frontmatter,
    skill_to_selectable,
)
from contextweaver.exceptions import CatalogError
from contextweaver.routing.cards import cards_for_route
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder


def _write_skill(
    root: Path,
    dir_name: str,
    frontmatter: dict[str, object],
    *,
    body: str = "Step-by-step skill body.",
    resources: dict[str, str] | None = None,
) -> Path:
    """Create a skill directory with a SKILL.md and optional resource files."""
    skill_dir = root / dir_name
    skill_dir.mkdir(parents=True)
    front = yaml.safe_dump(frontmatter, sort_keys=True).strip()
    (skill_dir / "SKILL.md").write_text(f"---\n{front}\n---\n{body}\n", encoding="utf-8")
    for rel, content in (resources or {}).items():
        target = skill_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return skill_dir


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


def test_parse_skill_frontmatter_minimal() -> None:
    front, body = parse_skill_frontmatter(
        "---\nname: pdf\ndescription: Work with PDFs.\n---\nBody here.\n", label="x"
    )
    assert front == {"name": "pdf", "description": "Work with PDFs."}
    assert body == "Body here."


def test_parse_skill_frontmatter_rich_keeps_unknown_keys() -> None:
    text = "---\nname: pdf\ndescription: d\nversion: 2\nlicense: MIT\n---\nBody"
    front, _body = parse_skill_frontmatter(text, label="x")
    assert front["version"] == 2
    assert front["license"] == "MIT"


def test_parse_skill_frontmatter_missing_open_fence_raises() -> None:
    with pytest.raises(CatalogError, match="opening '---'"):
        parse_skill_frontmatter("name: pdf\ndescription: d\n", label="x")


def test_parse_skill_frontmatter_missing_close_fence_raises() -> None:
    with pytest.raises(CatalogError, match="closing '---'"):
        parse_skill_frontmatter("---\nname: pdf\n", label="x")


def test_parse_skill_frontmatter_non_mapping_raises() -> None:
    with pytest.raises(CatalogError, match="must be a YAML mapping"):
        parse_skill_frontmatter("---\n- a\n- b\n---\nbody", label="x")


# ---------------------------------------------------------------------------
# Directory → SelectableItem
# ---------------------------------------------------------------------------


def test_skill_to_selectable_minimal(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path, "pdf", {"name": "pdf", "description": "Work with PDFs."})
    item = skill_to_selectable(skill_dir)
    assert item.kind == "skill"
    assert item.id == "skills:pdf"
    assert item.name == "pdf"
    assert item.namespace == "skills"
    assert item.tags == ["skill"]
    assert item.metadata["runtime"] == "agent-skills"
    assert item.metadata["skill_path"] == str(skill_dir)
    assert "frontmatter" not in item.metadata


def test_skill_to_selectable_extra_frontmatter_and_tags(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        "pdf",
        {"name": "pdf", "description": "d", "tags": ["docs", "pdf"], "version": "1.2"},
    )
    item = skill_to_selectable(skill_dir)
    assert set(item.tags) == {"skill", "docs", "pdf"}
    assert item.metadata["frontmatter"] == {"version": "1.2"}


def test_skill_to_selectable_namespace_override(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path, "pdf", {"name": "pdf", "description": "d"})
    assert skill_to_selectable(skill_dir, namespace="lib").namespace == "lib"


def test_skill_to_selectable_unicode_body(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path, "i18n", {"name": "i18n", "description": "Ünïcödé 数据"}, body="日本語の本文"
    )
    item = skill_to_selectable(skill_dir)
    assert item.description == "Ünïcödé 数据"


def test_skill_to_selectable_missing_name_raises(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path, "bad", {"description": "no name"})
    with pytest.raises(CatalogError, match="'name'"):
        skill_to_selectable(skill_dir)


def test_skill_to_selectable_missing_description_raises(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path, "bad", {"name": "x"})
    with pytest.raises(CatalogError, match="'description'"):
        skill_to_selectable(skill_dir)


def test_skill_to_selectable_missing_file_raises(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    with pytest.raises(CatalogError, match="Cannot read agent skill"):
        skill_to_selectable(tmp_path / "empty")


# ---------------------------------------------------------------------------
# Tree loading → Catalog
# ---------------------------------------------------------------------------


def test_load_skills_catalog_deterministic_order(tmp_path: Path) -> None:
    _write_skill(tmp_path, "zebra", {"name": "zebra", "description": "z"})
    _write_skill(tmp_path, "alpha", {"name": "alpha", "description": "a"})
    _write_skill(tmp_path, "nested/mid", {"name": "mid", "description": "m"})
    catalog = load_skills_catalog(tmp_path)
    ids = [item.id for item in catalog.all()]
    assert set(ids) == {"skills:zebra", "skills:alpha", "skills:mid"}
    assert len(ids) == 3


def test_load_skills_catalog_no_skills_raises(tmp_path: Path) -> None:
    with pytest.raises(CatalogError, match="No 'SKILL.md' files"):
        load_skills_catalog(tmp_path)


def test_load_skills_catalog_not_a_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(CatalogError, match="is not a directory"):
        load_skills_catalog(tmp_path / "does-not-exist")


# ---------------------------------------------------------------------------
# Lazy body hydration
# ---------------------------------------------------------------------------


def test_skill_body_source_round_trip(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "pdf",
        {"name": "pdf", "description": "d"},
        body="# Full body\nDetailed instructions.",
        resources={"reference.md": "ref", "scripts/run.py": "print(1)"},
    )
    catalog = load_skills_catalog(tmp_path)
    source = SkillBodySource.from_catalog(catalog)
    assert source.known_ids() == ["skills:pdf"]
    assert source.get_body("skills:pdf") == "# Full body\nDetailed instructions."
    assert source.get_resources("skills:pdf") == ["reference.md", "scripts/run.py"]


def test_skill_body_source_unknown_id_returns_none(tmp_path: Path) -> None:
    _write_skill(tmp_path, "pdf", {"name": "pdf", "description": "d"})
    source = SkillBodySource.from_catalog(load_skills_catalog(tmp_path))
    assert source.get_body("skills:missing") is None
    assert source.get_resources("skills:missing") is None


# ---------------------------------------------------------------------------
# End-to-end routing: only frontmatter is needed to shortlist
# ---------------------------------------------------------------------------


def test_skills_route_to_bounded_cards(tmp_path: Path) -> None:
    _write_skill(tmp_path, "pdf", {"name": "pdf", "description": "Extract text from PDF files."})
    _write_skill(tmp_path, "xlsx", {"name": "xlsx", "description": "Read spreadsheet data."})
    _write_skill(tmp_path, "img", {"name": "img", "description": "Resize and crop images."})
    catalog = load_skills_catalog(tmp_path)
    items = catalog.all()
    router = Router(TreeBuilder().build(items), items=items, beam_width=3)
    result = router.route("I need to pull text out of a PDF document")
    cards = cards_for_route(result.candidate_ids, catalog)
    assert cards, "expected at least one ChoiceCard"
    assert "skills:pdf" in result.candidate_ids

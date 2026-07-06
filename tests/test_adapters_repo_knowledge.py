"""Tests for contextweaver.adapters.repo_knowledge (issue #763)."""

from __future__ import annotations

from pathlib import Path

import pytest

from contextweaver.adapters.repo_knowledge import (
    classify_usage,
    load_repo_knowledge,
    repo_knowledge_nodes_to_context_items,
    select_repo_knowledge,
)
from contextweaver.exceptions import ConfigError


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_load_repo_knowledge_plain_markdown_fallback(tmp_path: Path) -> None:
    """A .md file with no frontmatter at all still becomes a candidate."""
    _write(tmp_path, "README.md", "# Plain doc\n\nNo frontmatter here.")
    bundle = load_repo_knowledge(tmp_path)
    assert len(bundle.nodes) == 1
    node = bundle.nodes[0]
    assert node.title == "README"
    assert "No frontmatter here." in node.text
    # No frontmatter means no title, so the shared "missing title" fallback
    # diagnostic fires — the file is still fully usable as plain Markdown.
    assert len(bundle.diagnostics) == 1
    assert "title" in bundle.diagnostics[0].message

    data = bundle.to_dict()
    assert data["truncated"] is False
    assert data["nodes"][0]["title"] == "README"
    assert data["diagnostics"][0]["message"] == bundle.diagnostics[0].message


def test_load_repo_knowledge_with_frontmatter(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "docs/agent-context/invariants.md",
        "---\nid: invariants\ntitle: Invariants\ntype: Architecture\n---\nBody.",
    )
    bundle = load_repo_knowledge(tmp_path)
    assert bundle.nodes[0].id == "invariants"
    assert bundle.nodes[0].source_path == "docs/agent-context/invariants.md"


def test_load_repo_knowledge_index_and_log_are_not_special(tmp_path: Path) -> None:
    """Unlike okf.load_okf_bundle, index.md/log.md carry no special meaning here."""
    _write(tmp_path, "index.md", "# Just a doc named index")
    bundle = load_repo_knowledge(tmp_path)
    assert len(bundle.nodes) == 1
    assert bundle.nodes[0].source_path == "index.md"


def test_load_repo_knowledge_not_a_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_repo_knowledge(tmp_path / "missing")


def test_load_repo_knowledge_max_files_guardrail_truncates(tmp_path: Path) -> None:
    for i in range(5):
        _write(tmp_path, f"doc_{i}.md", f"Doc {i}")
    bundle = load_repo_knowledge(tmp_path, max_files=2)
    assert len(bundle.nodes) == 2
    assert bundle.truncated is True


def test_load_repo_knowledge_max_total_bytes_guardrail_truncates(tmp_path: Path) -> None:
    _write(tmp_path, "big_a.md", "x" * 1000)
    _write(tmp_path, "big_b.md", "y" * 1000)
    bundle = load_repo_knowledge(tmp_path, max_total_bytes=1000)
    assert bundle.truncated is True
    assert len(bundle.nodes) < 2


def test_load_repo_knowledge_does_not_follow_links(tmp_path: Path) -> None:
    """AGENTS.md-style references must not force-load anything beyond the root."""
    _write(
        tmp_path,
        "AGENTS.md",
        "---\nid: agents\ntitle: Agents\nlinks: [some-other-repo-doc]\n---\nSee AGENTS.md.",
    )
    bundle = load_repo_knowledge(tmp_path)
    assert len(bundle.nodes) == 1  # the link is inert; nothing extra was loaded


# ---------------------------------------------------------------------------
# classify_usage — deterministic usage tags, not Phase values (issue #587 note)
# ---------------------------------------------------------------------------


def test_classify_usage_matches_keywords(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "debugging.md",
        "---\nid: dbg\ntitle: Debugging Guide\ntype: Guide\n---\nHow to debug.",
    )
    bundle = load_repo_knowledge(tmp_path)
    tags = classify_usage(bundle.nodes[0])
    assert tags == ["debugging"]


def test_classify_usage_no_match_returns_empty() -> None:
    from contextweaver.adapters._okf_io import KnowledgeNode

    node = KnowledgeNode(id="x", title="Nothing special", text="body")
    assert classify_usage(node) == []


def test_classify_usage_does_not_false_positive_on_substring() -> None:
    """ "test" must not match inside "latest" (word-boundary matching, not substring)."""
    from contextweaver.adapters._okf_io import KnowledgeNode

    node = KnowledgeNode(id="x", title="Deploying to the Latest Environment", text="body")
    assert classify_usage(node) == []


def test_classify_usage_prefix_match_still_works() -> None:
    """ "debug" must still match as a prefix of "debugging" (not exact-word-only)."""
    from contextweaver.adapters._okf_io import KnowledgeNode

    node = KnowledgeNode(id="x", title="Debugging tips", text="body")
    assert classify_usage(node) == ["debugging"]


def test_repo_knowledge_nodes_to_context_items_stamps_usage_tags(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "onboarding.md",
        "---\nid: onb\ntitle: Getting Started\ntags: [important]\n---\nQuickstart.",
    )
    bundle = load_repo_knowledge(tmp_path)
    items = repo_knowledge_nodes_to_context_items(bundle.nodes)
    assert "onboarding" in items[0].metadata["tags"]
    assert "important" in items[0].metadata["tags"]


def test_select_repo_knowledge_filters_by_usage_tag(tmp_path: Path) -> None:
    _write(tmp_path, "debug_doc.md", "---\nid: d\ntitle: Debugging\n---\nDebug guide.")
    _write(tmp_path, "release_doc.md", "---\nid: r\ntitle: Release process\n---\nRelease guide.")
    bundle = load_repo_knowledge(tmp_path)

    debugging_only = select_repo_knowledge(
        bundle.nodes, "guide", budget_tokens=10_000, usage_tag="debugging"
    )
    assert len(debugging_only) == 1
    assert debugging_only[0].metadata["_contextweaver"]["knowledge_source"]["id"] == "d"


def test_select_repo_knowledge_stamps_usage_tags_like_the_other_materialisation_path(
    tmp_path: Path,
) -> None:
    """select_repo_knowledge must stamp the same usage tags as
    repo_knowledge_nodes_to_context_items, so both materialisation paths
    produce a consistent metadata shape for the same node."""
    _write(tmp_path, "debug_doc.md", "---\nid: d\ntitle: Debugging Guide\n---\nDebug guide.")
    bundle = load_repo_knowledge(tmp_path)

    selected = select_repo_knowledge(bundle.nodes, "debug", budget_tokens=10_000)
    assert "debugging" in selected[0].metadata["tags"]


def test_select_repo_knowledge_deterministic(tmp_path: Path) -> None:
    _write(tmp_path, "a.md", "---\nid: a\ntitle: A\n---\nAlpha content.")
    _write(tmp_path, "b.md", "---\nid: b\ntitle: B\n---\nBeta content.")
    bundle = load_repo_knowledge(tmp_path)
    first = select_repo_knowledge(bundle.nodes, "zzz-no-match", budget_tokens=10_000)
    second = select_repo_knowledge(bundle.nodes, "zzz-no-match", budget_tokens=10_000)
    assert [i.id for i in first] == [i.id for i in second]

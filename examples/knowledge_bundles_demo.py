"""Knowledge-bundle context sources demo (issues #736, #763, #767, #776).

Demonstrates the four OKF-style Markdown-plus-frontmatter knowledge-source
adapters, all built on one shared permissive parsing core:

1. OKF bundle loading and relevance-ranked selection (:mod:`contextweaver.adapters.okf`).
2. Repository-knowledge ingestion with usage-tag classification
   (:mod:`contextweaver.adapters.repo_knowledge`).
3. Lifecycle-aware LessonWeaver lesson selection — excluding rejected/
   deprecated/unreviewed lessons by default (:mod:`contextweaver.adapters.lessons`).
4. ExpertisePack loading with deterministic conflict detection
   (:mod:`contextweaver.adapters.expertise_pack`).

Builds tiny fixture bundles in a temp directory so the demo runs offline with
no network and no extra dependencies (PyYAML is a core dep).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from contextweaver.adapters.expertise_pack import detect_conflicts, load_expertise_pack
from contextweaver.adapters.lessons import (
    LessonSelectionPolicy,
    load_lesson_bundle,
    select_lessons,
)
from contextweaver.adapters.okf import load_okf_bundle, select_knowledge
from contextweaver.adapters.repo_knowledge import classify_usage, load_repo_knowledge


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _demo_okf(root: Path) -> None:
    _write(root, "concept_routing.md", "---\nid: routing\ntitle: Routing\n---\nBounded routing.")
    _write(
        root, "concept_firewall.md", "---\nid: firewall\ntitle: Firewall\n---\nContext firewall."
    )

    bundle = load_okf_bundle(root)
    print(f"[okf] loaded {len(bundle.nodes)} concepts")
    items = select_knowledge(bundle.nodes, "firewall", budget_tokens=1000)
    print(f"[okf] top match for 'firewall': {items[0].id}")


def _demo_repo_knowledge(root: Path) -> None:
    _write(root, "docs/quickstart.md", "---\nid: qs\ntitle: Getting Started\n---\nQuickstart.")
    _write(root, "docs/debugging.md", "---\nid: dbg\ntitle: Debug Guide\n---\nHow to debug.")

    bundle = load_repo_knowledge(root)
    for node in bundle.nodes:
        print(f"[repo_knowledge] {node.id}: usage tags = {classify_usage(node)}")


def _demo_lessons(root: Path) -> None:
    _write(root, "active.md", "---\nid: l1\nstatus: active\nscope: project\n---\nUse small APIs.")
    _write(root, "rejected.md", "---\nid: l2\nstatus: rejected\n---\nAvoid singletons (rejected).")

    nodes, _diagnostics = load_lesson_bundle(root)
    items, excluded = select_lessons(
        nodes, "api design", budget_tokens=1000, policy=LessonSelectionPolicy()
    )
    print(f"[lessons] {len(items)} eligible lesson(s), {len(excluded)} excluded")
    for exclusion in excluded:
        print(f"[lessons] excluded {exclusion.node_id}: {exclusion.reason}")


def _demo_expertise_pack(root: Path) -> None:
    _write(root, "index.md", '---\nversion: "1.0"\n---\nSample pack.')
    _write(root, "rest.md", "---\nid: c1\nkey: api-style\n---\nPrefer REST.")
    _write(root, "graphql.md", "---\nid: c2\nkey: api-style\n---\nPrefer GraphQL.")

    pack = load_expertise_pack(root)
    findings = detect_conflicts(pack.nodes)
    print(f"[expertise_pack] loaded {len(pack.nodes)} constraint(s), version={pack.version}")
    for finding in findings:
        print(f"[expertise_pack] conflict under key {finding.key!r}: {finding.reason}")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _demo_okf(root / "okf")
        _demo_repo_knowledge(root / "repo")
        _demo_lessons(root / "lessons")
        _demo_expertise_pack(root / "expertise")


if __name__ == "__main__":
    main()

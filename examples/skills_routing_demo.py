"""Agent Skills (SKILL.md) routing demo (issue #545).

Demonstrates the two contextweaver entry points for a skills library:

1. Routing — load a directory of ``SKILL.md`` skills (only their frontmatter)
   into a :class:`~contextweaver.routing.catalog.Catalog`, build a routing
   graph, and score a query against it to get a bounded shortlist of skills.

2. Lazy body hydration — once a skill is selected, resolve its full Markdown
   body (and bundled resource files) on demand via
   :class:`~contextweaver.adapters.agent_skills.SkillBodySource`, so the large
   bodies never enter the route prompt.

Builds a tiny fixture skills library in a temp directory so the demo runs
offline with no network and no extra dependencies (PyYAML is a core dep).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from contextweaver.adapters.agent_skills import SkillBodySource, load_skills_catalog
from contextweaver.routing.cards import cards_for_route, format_card_for_prompt
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder

SKILLS: dict[str, tuple[str, str]] = {
    "pdf": ("Extract text and tables from PDF documents.", "# PDF skill\nUse pdfplumber..."),
    "xlsx": ("Read and summarise spreadsheet data.", "# XLSX skill\nUse openpyxl..."),
    "images": ("Resize, crop, and convert image files.", "# Image skill\nUse Pillow..."),
    "git": ("Inspect git history and craft commit messages.", "# Git skill\nUse subprocess..."),
}


def _build_library(root: Path) -> None:
    for name, (description, body) in SKILLS.items():
        skill_dir = root / name
        skill_dir.mkdir(parents=True)
        front = f"name: {name}\ndescription: {description}"
        (skill_dir / "SKILL.md").write_text(f"---\n{front}\n---\n{body}\n", encoding="utf-8")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _build_library(root)

        catalog = load_skills_catalog(root)
        body_source = SkillBodySource.from_catalog(catalog)
        items = catalog.all()
        router = Router(TreeBuilder().build(items), items=items, beam_width=3)

        query = "I need to pull the tables out of a PDF report"
        print(f"Loaded {len(items)} skills (frontmatter only).")
        print(f"Query: {query!r}\n")

        result = router.route(query)
        cards = cards_for_route(result.candidate_ids, catalog)
        print("Bounded skill shortlist:")
        for card in cards:
            print(format_card_for_prompt(card))

        chosen = result.candidate_ids[0]
        print(f"\nSelected skill: {chosen}")
        print("Hydrated body (loaded only now, on selection):")
        print(body_source.get_body(chosen))


if __name__ == "__main__":
    main()

"""Filesystem + parsing helpers for the Agent Skills adapter (issue #545).

Private module backing :mod:`contextweaver.adapters.agent_skills`; holds the
SKILL.md frontmatter parser, the deterministic skill-directory walk, and the
lazy :class:`SkillBodySource`.  Kept separate so ``agent_skills.py`` stays
within the ≤300-line module ceiling.  ``parse_skill_frontmatter`` and
``SkillBodySource`` are re-exported as public API from ``agent_skills``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from contextweaver.exceptions import CatalogError
from contextweaver.routing.catalog import Catalog

#: The required entrypoint filename inside a skill directory.
SKILL_FILENAME = "SKILL.md"


def parse_skill_frontmatter(text: str, *, label: str) -> tuple[dict[str, Any], str]:
    """Split a SKILL.md document into its YAML frontmatter and Markdown body.

    The document must open with a ``---`` fence, contain a closing ``---``
    fence, and carry a YAML mapping between them.

    Args:
        text: The full SKILL.md file contents.
        label: Locator (e.g. the skill path) used in error messages.

    Returns:
        A ``(frontmatter, body)`` tuple — the parsed mapping and the trailing
        Markdown body (leading blank lines stripped).

    Raises:
        CatalogError: If the frontmatter fence is missing or the frontmatter
            is not a YAML mapping.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise CatalogError(f"Agent skill {label} is missing the opening '---' frontmatter fence.")
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            raw_front = "\n".join(lines[1:idx])
            body = "\n".join(lines[idx + 1 :]).lstrip("\n")
            try:
                loaded = yaml.safe_load(raw_front) if raw_front.strip() else {}
            except yaml.YAMLError as exc:
                raise CatalogError(
                    f"Agent skill {label} has invalid YAML frontmatter: {exc}"
                ) from exc
            if not isinstance(loaded, dict):
                raise CatalogError(
                    f"Agent skill {label} frontmatter must be a YAML mapping; "
                    f"got {type(loaded).__name__}."
                )
            return loaded, body
    raise CatalogError(f"Agent skill {label} is missing the closing '---' frontmatter fence.")


def discover_skill_dirs(root: Path) -> list[Path]:
    """Return every directory under *root* that contains a ``SKILL.md``, sorted.

    The root itself is included when it holds a ``SKILL.md`` directly.  The walk
    is sorted by path so catalog ordering is deterministic.
    """
    found: list[Path] = []
    if (root / SKILL_FILENAME).is_file():
        found.append(root)
    found.extend(p.parent for p in sorted(root.rglob(SKILL_FILENAME)) if p.parent != root)
    return found


class SkillBodySource:
    """Resolve a skill's Markdown body and bundled resource files on demand.

    Mirrors :class:`contextweaver.routing.hydration.SchemaSource`: the routing
    catalog carries only the frontmatter, and this source hydrates the full
    body for the *selected* skill so large bodies never enter the route prompt.

    Build one from a catalog produced by
    :func:`contextweaver.adapters.agent_skills.load_skills_catalog` (it reads
    each item's ``metadata["skill_path"]``) and call :meth:`get_body` /
    :meth:`get_resources` after a skill is chosen.

    Bodies are arbitrary untrusted Markdown: ingest the resolved text through
    the context firewall and treat it with the same caution as tool
    descriptions.
    """

    __slots__ = ("_paths",)

    def __init__(self, paths: dict[str, Path] | None = None) -> None:
        """Initialise from a mapping of skill id → skill directory.

        Args:
            paths: Optional mapping of skill ``SelectableItem.id`` to its
                on-disk directory.  Usually built via :meth:`from_catalog`.
        """
        self._paths: dict[str, Path] = dict(paths) if paths else {}

    @classmethod
    def from_catalog(cls, catalog: Catalog) -> SkillBodySource:
        """Build a source from a skills catalog via each item's ``skill_path``.

        Args:
            catalog: A catalog of ``kind="skill"`` items carrying
                ``metadata["skill_path"]`` (as produced by
                ``load_skills_catalog``).

        Returns:
            A :class:`SkillBodySource` keyed by skill id.
        """
        paths: dict[str, Path] = {}
        for item in catalog.all():
            skill_path = (item.metadata or {}).get("skill_path")
            if isinstance(skill_path, str) and skill_path:
                paths[item.id] = Path(skill_path)
        return cls(paths)

    def get_body(self, skill_id: str) -> str | None:
        """Return the Markdown body for *skill_id*, or ``None`` if unknown.

        Args:
            skill_id: The skill ``SelectableItem.id`` to resolve.

        Returns:
            The SKILL.md body (frontmatter stripped), or ``None`` when the id
            is not registered.

        Raises:
            CatalogError: If the skill's ``SKILL.md`` cannot be read or parsed.
        """
        directory = self._paths.get(skill_id)
        if directory is None:
            return None
        skill_file = directory / SKILL_FILENAME
        try:
            text = skill_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise CatalogError(f"Cannot read agent skill body at {skill_file!s}: {exc}") from exc
        _front, body = parse_skill_frontmatter(text, label=str(skill_file))
        return body

    def get_resources(self, skill_id: str) -> list[str] | None:
        """Return the bundled resource files for *skill_id*, or ``None``.

        Lists every file in the skill directory other than ``SKILL.md``, as
        paths relative to the skill directory, sorted.  Loading the resource
        contents is left to the caller (progressive disclosure).

        Args:
            skill_id: The skill ``SelectableItem.id`` to resolve.

        Returns:
            A sorted list of relative resource paths, or ``None`` when the id
            is not registered.
        """
        directory = self._paths.get(skill_id)
        if directory is None:
            return None
        return [
            str(p.relative_to(directory))
            for p in sorted(directory.rglob("*"))
            if p.is_file() and p.name != SKILL_FILENAME
        ]

    def known_ids(self) -> list[str]:
        """Return all skill ids registered in this source, sorted."""
        return sorted(self._paths)

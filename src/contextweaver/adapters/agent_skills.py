"""Agent Skills (SKILL.md) catalog adapter for contextweaver (issue #545).

Loads [Agent Skills](https://github.com/anthropics/skills) directories — the
open ``SKILL.md`` format — into the routing catalog as
:class:`~contextweaver.types.SelectableItem`s.  Each skill's YAML frontmatter
(``name`` / ``description`` required, plus optional metadata) becomes routable
card metadata; the full Markdown body is *not* loaded into the route prompt.
Instead a :class:`SkillBodySource` resolves the body (and the list of bundled
resource files) lazily on selection — the same progressive-disclosure pattern
:mod:`contextweaver.routing.hydration` applies to tool schemas.

"Which of my 200 skills applies here?" is the same context-rot problem as
"which of my 200 tools," and contextweaver answers it deterministically: load
only frontmatter to route, hydrate the chosen skill's body on demand.

Two surfaces:

1. **Catalog** — :func:`skill_to_selectable`, :func:`load_skills_catalog`
   convert a skill directory / a tree of skill directories into
   ``kind="skill"`` catalog items (sorted deterministically).
2. **Lazy body hydration** — :class:`SkillBodySource` mirrors
   :class:`~contextweaver.routing.hydration.SchemaSource`: ``get_body(id)`` and
   ``get_resources(id)`` read the chosen skill's body / resource list on
   demand.  Skill bodies are arbitrary untrusted Markdown — route the resolved
   body through the firewall path when ingesting it as context, and treat the
   text with the same caution as upstream tool descriptions (cross-ref #480).

This module is a pure adapter: it parses the SKILL.md layout (file I/O at the
``load_*`` boundary, PyYAML is a core dependency) and imports no third-party
agent runtime.  Skill *execution* is out of scope — contextweaver routes and
hydrates only.  Marketplace fetch/install is out of scope; local directories
only.  The frontmatter parser, directory walk, and :class:`SkillBodySource`
live in the private :mod:`._agent_skills_io` helper to keep this module within
the ≤300-line ceiling.

Tracks the Agent Skills layout as of the late-2025 open spec: a directory
containing ``SKILL.md`` with ``---``-delimited YAML frontmatter plus optional
support files.  The parser tolerates unknown frontmatter keys so spec growth
does not break loading.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from contextweaver.adapters._agent_skills_io import (
    SKILL_FILENAME,
    SkillBodySource,
    discover_skill_dirs,
    parse_skill_frontmatter,
)
from contextweaver.adapters._framework_common import collect_tags, require_name_description
from contextweaver.exceptions import CatalogError
from contextweaver.routing.catalog import Catalog
from contextweaver.types import SelectableItem

logger = logging.getLogger("contextweaver.adapters")

_FALLBACK_NS = "skills"
_ID_PREFIX = "skills"
_RUNTIME = "agent-skills"
#: Tag stamped on every imported skill so callers can gate routing with
#: ``Router.route(allowed_tags={"skill"})`` or filter skills out explicitly.
SKILL_TAG = "skill"
#: Frontmatter keys mapped to dedicated ``SelectableItem`` fields rather than
#: copied verbatim into ``metadata``.
_RESERVED_FRONTMATTER = frozenset({"name", "description", "tags"})


def skill_to_selectable(skill_dir: str | Path, *, namespace: str | None = None) -> SelectableItem:
    """Convert a single skill directory to a :class:`SelectableItem`.

    Reads ``{skill_dir}/SKILL.md``, parses its frontmatter, and maps:

    - ``name`` (required) → ``id`` (``"skills:{name}"``) and ``name``.
    - ``description`` (required) → ``description`` (the routable summary).
    - ``tags`` (optional list) → tags (merged with the ``"skill"`` tag).
    - every other frontmatter key → ``metadata["frontmatter"]`` verbatim.

    The Markdown body is deliberately **not** read here — only the frontmatter
    is needed to route.  Use a :class:`SkillBodySource` to hydrate the body on
    selection.

    Args:
        skill_dir: Path to a directory containing a ``SKILL.md`` file.
        namespace: Explicit namespace override.  Defaults to ``"skills"``.

    Returns:
        A :class:`SelectableItem` with ``kind="skill"``.  ``metadata`` carries
        ``skill_path`` (the directory), ``runtime="agent-skills"``, and any
        non-reserved frontmatter under ``frontmatter``.

    Raises:
        CatalogError: If the directory has no ``SKILL.md``, the file cannot be
            read, the frontmatter is malformed, or ``name`` / ``description``
            are missing.
    """
    directory = Path(skill_dir)
    skill_file = directory / SKILL_FILENAME
    try:
        text = skill_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise CatalogError(f"Cannot read agent skill at {skill_file!s}: {exc}") from exc

    frontmatter, _body = parse_skill_frontmatter(text, label=str(skill_file))
    raw_name, raw_description = require_name_description(frontmatter, label="Agent Skills")

    ns = namespace if namespace is not None else _FALLBACK_NS
    tags = collect_tags(frontmatter.get("tags"), fallback=SKILL_TAG)

    extra = {k: v for k, v in frontmatter.items() if k not in _RESERVED_FRONTMATTER}
    metadata: dict[str, Any] = {"runtime": _RUNTIME, "skill_path": str(directory)}
    if extra:
        metadata["frontmatter"] = extra

    logger.debug("skill_to_selectable: name=%s, ns=%s, tags=%s", raw_name, ns, tags)
    return SelectableItem(
        id=f"{_ID_PREFIX}:{raw_name}",
        kind="skill",
        name=raw_name,
        description=raw_description,
        tags=tags,
        namespace=ns,
        metadata=metadata,
    )


def load_skills_catalog(root_dir: str | Path, *, namespace: str | None = None) -> Catalog:
    """Load a tree of skill directories into a :class:`Catalog`.

    Recursively discovers every directory containing a ``SKILL.md`` under
    *root_dir* (in sorted path order) and registers each as a ``kind="skill"``
    catalog item.

    Args:
        root_dir: A skills library root, or a single skill directory.
        namespace: Optional namespace override applied to every skill.

    Returns:
        A populated :class:`~contextweaver.routing.catalog.Catalog`.

    Raises:
        CatalogError: If *root_dir* is not a directory, contains no skills, or
            any contained skill is invalid (incl. duplicate skill names).
    """
    root = Path(root_dir)
    if not root.is_dir():
        raise CatalogError(f"Agent skills root {root!s} is not a directory.")
    skill_dirs = discover_skill_dirs(root)
    if not skill_dirs:
        raise CatalogError(f"No '{SKILL_FILENAME}' files found under {root!s}.")
    catalog = Catalog()
    for skill_dir in skill_dirs:
        catalog.register(skill_to_selectable(skill_dir, namespace=namespace))
    logger.debug("load_skills_catalog: registered %d skills from %s", len(skill_dirs), root)
    return catalog


__all__ = [
    "SKILL_FILENAME",
    "SKILL_TAG",
    "SkillBodySource",
    "load_skills_catalog",
    "parse_skill_frontmatter",
    "skill_to_selectable",
]

"""Private frontmatter-bundle parsing core backing the knowledge-source adapters.

Shared by :mod:`.okf`, :mod:`.repo_knowledge`, :mod:`.lessons`, and
:mod:`.expertise_pack` (issues #736/#763/#767/#776): a permissive
Markdown-plus-YAML-frontmatter node parser and a deterministic directory
walk. Kept private (materialisation split out to :mod:`._okf_materialize`)
so each public adapter module stays within the ≤300-line convention.

Parsing is deliberately permissive (issue #736): a missing fence, invalid
YAML, a non-mapping frontmatter value, or a non-UTF-8 file degrades to a
:class:`LoadDiagnostic` plus a best-effort node, never an exception.
Callers wanting fail-fast behavior pass ``on_invalid="raise"`` to the
public loaders, which turn diagnostics into :class:`ConfigError`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from contextweaver.adapters._okf_coerce import (
    coerce_expires,
    coerce_float,
    coerce_str_list,
    json_safe,
)
from contextweaver.exceptions import ConfigError
from contextweaver.types import Sensitivity

#: Bundle-level files with documented, non-concept behavior (issue #736):
#: ``index.md`` is optional overview/bundle metadata, ``log.md`` is optional
#: bundle history. Neither is treated as ordinary concept content by default.
INDEX_FILENAME = "index.md"
LOG_FILENAME = "log.md"

#: Frontmatter keys mapped onto named :class:`KnowledgeNode` fields. Every
#: other key is preserved verbatim under ``KnowledgeNode.frontmatter``.
_KNOWN_KEYS = frozenset(
    {
        "id",
        "type",
        "title",
        "description",
        "resource",
        "tags",
        "timestamp",
        "links",
        "scope",
        "status",
        "confidence",
        "expires_at",
        "sensitivity",
    }
)


@dataclass(frozen=True)
class LoadDiagnostic:
    """A recoverable finding surfaced while loading a knowledge bundle.

    ``level`` is ``"warning"`` (degraded but usable) or ``"error"`` (node
    dropped or bundle rejected, only reachable with ``on_invalid="raise"``).
    ``path`` is the bundle-relative POSIX path the finding is about.
    """

    level: str
    path: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {"level": self.level, "path": self.path, "message": self.message}


@dataclass
class KnowledgeNode:
    """A single concept/lesson/expertise node parsed from a frontmatter bundle.

    Field vocabulary mirrors :class:`~contextweaver.context.memory_types.MemoryEntry`
    (``scope``/``confidence``/``expires_at``/``sensitivity``) so lifecycle-aware
    selection (#767) reuses the same shape. ``id`` is the frontmatter ``id``
    when present, else derived from ``source_path``. ``title`` falls back to
    the filename stem when absent. ``node_type`` (frontmatter ``type``) is
    preserved verbatim — no fixed enum (#736 "avoid overfitting"). ``status``
    is primarily consumed by #767. ``sensitivity`` defaults to
    :attr:`~contextweaver.types.Sensitivity.internal` — never ``public`` by
    default. ``frontmatter`` holds every key not mapped onto a named field
    above, preserved verbatim.
    """

    id: str
    title: str
    text: str
    node_type: str = ""
    source_path: str = ""
    tags: list[str] = field(default_factory=list)
    scope: str = ""
    status: str = ""
    confidence: float = 1.0
    timestamp: float = 0.0
    expires_at: float | None = None
    links: list[str] = field(default_factory=list)
    sensitivity: Sensitivity = Sensitivity.internal
    frontmatter: dict[str, Any] = field(default_factory=dict)

    def is_expired(self, *, now: float | None = None) -> bool:
        """Return ``True`` past ``expires_at``.

        No wall-clock fallback (#617): callers must inject ``now``.
        """
        if self.expires_at is None or now is None:
            return False
        return now >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "id": self.id,
            "title": self.title,
            "text": self.text,
            "node_type": self.node_type,
            "source_path": self.source_path,
            "tags": list(self.tags),
            "scope": self.scope,
            "status": self.status,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
            "expires_at": self.expires_at,
            "links": list(self.links),
            "sensitivity": self.sensitivity.value,
            "frontmatter": json_safe(self.frontmatter),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KnowledgeNode:
        """Deserialise from a JSON-compatible dict produced by :meth:`to_dict`."""
        sensitivity_raw = data.get("sensitivity", Sensitivity.internal.value)
        try:
            sensitivity = Sensitivity(sensitivity_raw)
        except (TypeError, ValueError) as exc:
            msg = f"KnowledgeNode: invalid sensitivity {sensitivity_raw!r}"
            raise ConfigError(msg) from exc
        return cls(
            id=str(data["id"]),
            title=str(data["title"]),
            text=str(data["text"]),
            node_type=str(data.get("node_type", "")),
            source_path=str(data.get("source_path", "")),
            tags=list(data.get("tags", [])),
            scope=str(data.get("scope", "")),
            status=str(data.get("status", "")),
            confidence=float(data.get("confidence", 1.0)),
            timestamp=float(data.get("timestamp", 0.0)),
            expires_at=(None if data.get("expires_at") is None else float(data["expires_at"])),
            links=list(data.get("links", [])),
            sensitivity=sensitivity,
            frontmatter=dict(data.get("frontmatter", {})),
        )


def parse_markdown_frontmatter(text: str) -> tuple[dict[str, Any], str, str | None]:
    """Split *text* into ``(frontmatter, body, error)``. Never raises (issue #736).

    A missing opening fence means "plain Markdown, no frontmatter" and
    returns ``({}, text, None)``. A missing closing fence, invalid YAML, or a
    non-mapping frontmatter value degrades to ``({}, text, <message>)`` so the
    caller can still ingest the file as plain text while surfacing a
    diagnostic. ``error`` is ``None`` on a clean parse.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text, None
    for idx in range(1, len(lines)):
        if lines[idx].strip() != "---":
            continue
        raw_front = "\n".join(lines[1:idx])
        body = "\n".join(lines[idx + 1 :]).lstrip("\n")
        try:
            loaded = yaml.safe_load(raw_front) if raw_front.strip() else {}
        except yaml.YAMLError as exc:
            return {}, text, f"invalid YAML frontmatter: {exc}"
        if not isinstance(loaded, dict):
            return {}, text, f"frontmatter must be a YAML mapping; got {type(loaded).__name__}"
        return loaded, body, None
    return {}, text, "missing closing '---' frontmatter fence"


def discover_concept_files(root: Path) -> list[Path]:
    """Return concept Markdown files under *root*, sorted, deterministic.

    Excludes :data:`INDEX_FILENAME` / :data:`LOG_FILENAME` wherever they
    appear — those are bundle metadata/history, not concept content.
    """
    return sorted(
        p for p in root.rglob("*.md") if p.name.lower() not in (INDEX_FILENAME, LOG_FILENAME)
    )


def read_markdown_text(path: Path) -> str:
    """Read *path* permissively — never raises ``UnicodeDecodeError``."""
    return path.read_bytes().decode("utf-8", errors="replace")


def node_from_markdown(
    text: str, *, source_path: str
) -> tuple[KnowledgeNode, LoadDiagnostic | None]:
    """Parse one Markdown-plus-frontmatter file into a :class:`KnowledgeNode`.

    Returns a ``(node, diagnostic)`` tuple; ``diagnostic`` is ``None`` on a
    clean parse, otherwise it describes the degradation the node underwent —
    the node itself is always usable, since parsing never raises here.
    """
    frontmatter, body, error = parse_markdown_frontmatter(text)
    diagnostic = LoadDiagnostic(level="warning", path=source_path, message=error) if error else None

    raw_id = frontmatter.get("id")
    node_id = str(raw_id) if raw_id else source_path

    raw_title = frontmatter.get("title")
    if not raw_title:
        title = Path(source_path).stem
        diagnostic = diagnostic or LoadDiagnostic(
            level="warning", path=source_path, message="missing 'title'; using filename stem"
        )
    else:
        title = str(raw_title)

    sensitivity_raw = frontmatter.get("sensitivity")
    try:
        sensitivity = Sensitivity(sensitivity_raw) if sensitivity_raw else Sensitivity.internal
    except ValueError:
        sensitivity = Sensitivity.internal

    extra = {k: json_safe(v) for k, v in frontmatter.items() if k not in _KNOWN_KEYS}

    expires_at, expires_ok = coerce_expires(frontmatter.get("expires_at"))
    if not expires_ok:
        diagnostic = diagnostic or LoadDiagnostic(
            level="warning",
            path=source_path,
            message="uncoercible 'expires_at' (expected epoch seconds); treating as no expiry",
        )

    node = KnowledgeNode(
        id=node_id,
        title=title,
        text=body,
        node_type=str(frontmatter.get("type", "")),
        source_path=source_path,
        tags=coerce_str_list(frontmatter.get("tags")),
        scope=str(frontmatter.get("scope", "")),
        status=str(frontmatter.get("status", "")),
        confidence=coerce_float(frontmatter.get("confidence"), 1.0),
        timestamp=coerce_float(frontmatter.get("timestamp"), 0.0),
        expires_at=expires_at,
        links=coerce_str_list(frontmatter.get("links")),
        sensitivity=sensitivity,
        frontmatter=extra,
    )
    return node, diagnostic


def validate_links(nodes: list[KnowledgeNode]) -> list[LoadDiagnostic]:
    """Return a diagnostic for every link that resolves to no known node.

    Link targets may legitimately point outside the bundle (external URLs,
    other repos), so an unresolved link is a ``"warning"``, never an error.
    """
    known_ids = {n.id for n in nodes} | {n.source_path for n in nodes}
    diagnostics: list[LoadDiagnostic] = []
    for node in nodes:
        for link in node.links:
            if link in known_ids or "://" in link:
                continue
            diagnostics.append(
                LoadDiagnostic(
                    level="warning",
                    path=node.source_path,
                    message=f"broken link: {link!r} does not resolve to a known node",
                )
            )
    return diagnostics


__all__ = [
    "INDEX_FILENAME",
    "LOG_FILENAME",
    "KnowledgeNode",
    "LoadDiagnostic",
    "discover_concept_files",
    "node_from_markdown",
    "parse_markdown_frontmatter",
    "read_markdown_text",
    "validate_links",
]

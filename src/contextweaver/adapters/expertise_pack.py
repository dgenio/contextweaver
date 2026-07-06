"""ExpertisePacks as bounded context sources (issue #776).

An ExpertisePack is a directory bundle of constraint/assumption/
verification/failure-mode nodes, parsed through the same OKF-style
frontmatter core as :mod:`.okf` / :mod:`.repo_knowledge` / :mod:`.lessons`.
Each node's domain-specific ``key`` (e.g. ``"api-style"``,
``"verification-command"``) is a plain frontmatter field preserved by the
generic loader as metadata — this module is what gives ``key`` its meaning.

Schema scope (deliberately limited): the canonical pack schema is tracked
externally at ``dgenio/weaver-spec#184``, which is out of this repo's
session scope to fetch. This module validates pack **structure** (an
``index.md`` declaring a ``version`` string, every node carrying a ``key``)
rather than the full external schema — see the module-level ``TODO`` below
for the seam to bind it later.

Conflict detection is deterministic-only (repo convention: no model calls in
core paths). It flags contradicting constraint values under the same
``key`` and applicability mismatches; it does **not** perform natural-language
contradiction inference — that would require an LLM path and belongs behind
the existing ``call_fn`` plugin cluster (out of scope here).

TODO(#776 follow-up): bind ``dgenio/weaver-spec#184``'s canonical JSON Schema
once accessible, replacing the structural checks below with real validation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from contextweaver.adapters._okf_io import (
    KnowledgeNode,
    LoadDiagnostic,
    discover_concept_files,
    node_from_markdown,
    read_markdown_text,
)
from contextweaver.adapters._okf_materialize import node_to_context_item
from contextweaver.exceptions import ConfigError
from contextweaver.protocols import TokenEstimator
from contextweaver.types import ContextItem

SOURCE_KIND = "expertise_pack"

#: Frontmatter file declaring the pack's schema version (mirrors OKF's
#: index.md convention). Required for a pack to validate cleanly.
VERSION_FILENAME = "index.md"


def _as_list(value: Any) -> list[str]:  # noqa: ANN401 -- opaque frontmatter value
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)] if value else []


@dataclass(frozen=True)
class ConflictFinding:
    """A deterministic contradiction detected between pack constraints.

    Attributes:
        key: The shared frontmatter ``key`` the conflicting nodes disagree on.
        node_ids: The IDs of the conflicting nodes, sorted.
        reason: Human-readable explanation.
    """

    key: str
    node_ids: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {"key": self.key, "node_ids": list(self.node_ids), "reason": self.reason}


@dataclass
class ExpertisePack:
    """A loaded, structurally-validated ExpertisePack bundle.

    Attributes:
        nodes: Constraint/assumption/verification/failure-mode nodes.
        version: The pack's declared schema version, or ``None`` if
            ``index.md`` was absent or lacked a ``version`` field.
        diagnostics: Every recoverable finding from the load (missing
            ``key``, missing version, ...).
    """

    nodes: list[KnowledgeNode] = field(default_factory=list)
    version: str | None = None
    diagnostics: list[LoadDiagnostic] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "version": self.version,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
        }


def load_expertise_pack(
    path: str | Path, *, on_invalid: Literal["warn", "raise"] = "warn"
) -> ExpertisePack:
    """Load and structurally validate an ExpertisePack directory.

    Structural validation (issue #776 acceptance criteria — "validate pack
    version/schema before use"): an ``index.md`` must exist and declare a
    ``version``; every other node must carry a frontmatter ``key``. Neither
    check is the full external weaver-spec schema (see module docstring).

    Raises:
        ConfigError: If *path* is not a directory, or (``on_invalid="raise"``)
            a structural check failed.
    """
    root = Path(path)
    if not root.is_dir():
        msg = f"load_expertise_pack: not a directory: {root}"
        raise ConfigError(msg)

    diagnostics: list[LoadDiagnostic] = []
    version = _load_pack_version(root, diagnostics, on_invalid=on_invalid)

    nodes: list[KnowledgeNode] = []
    for file_path in discover_concept_files(root):
        source_path = file_path.relative_to(root).as_posix()
        node, diagnostic = node_from_markdown(
            read_markdown_text(file_path), source_path=source_path
        )
        if diagnostic:
            _record(diagnostics, diagnostic, on_invalid=on_invalid, label="load_expertise_pack")
        if "key" not in node.frontmatter:
            missing_key = LoadDiagnostic(
                level="warning",
                path=source_path,
                message="missing 'key'; not a valid constraint node",
            )
            _record(diagnostics, missing_key, on_invalid=on_invalid, label="load_expertise_pack")
        nodes.append(node)

    return ExpertisePack(nodes, version=version, diagnostics=diagnostics)


def _record(
    diagnostics: list[LoadDiagnostic],
    diagnostic: LoadDiagnostic,
    *,
    on_invalid: Literal["warn", "raise"],
    label: str,
) -> None:
    if on_invalid == "raise":
        msg = f"{label}: {diagnostic.path}: {diagnostic.message}"
        raise ConfigError(msg)
    diagnostics.append(diagnostic)


def _load_pack_version(
    root: Path, diagnostics: list[LoadDiagnostic], *, on_invalid: Literal["warn", "raise"]
) -> str | None:
    index_path = root / VERSION_FILENAME
    if not index_path.is_file():
        _record(
            diagnostics,
            LoadDiagnostic(level="warning", path=VERSION_FILENAME, message="missing index.md"),
            on_invalid=on_invalid,
            label="load_expertise_pack",
        )
        return None
    node, diagnostic = node_from_markdown(
        read_markdown_text(index_path), source_path=VERSION_FILENAME
    )
    if diagnostic:
        _record(diagnostics, diagnostic, on_invalid=on_invalid, label="load_expertise_pack")
    version = node.frontmatter.get("version")
    if not version:
        _record(
            diagnostics,
            LoadDiagnostic(
                level="warning", path=VERSION_FILENAME, message="index.md missing 'version'"
            ),
            on_invalid=on_invalid,
            label="load_expertise_pack",
        )
        return None
    return str(version)


def _applies(node: KnowledgeNode, task_tags: set[str] | None) -> bool:
    if task_tags is None:
        return True
    if set(_as_list(node.frontmatter.get("not_applicable_to"))) & task_tags:
        return False
    applicable = set(_as_list(node.frontmatter.get("applicable_to")))
    return not applicable or bool(applicable & task_tags)


def detect_conflicts(
    nodes: list[KnowledgeNode],
    *,
    task_tags: set[str] | None = None,
    now: float | None = None,
) -> list[ConflictFinding]:
    """Return deterministic contradictions among *nodes* sharing a ``key``.

    Only live (non-expired) and, when *task_tags* is given, applicable nodes
    are compared. Two nodes conflict when they share a ``key`` but disagree
    on constraint text — this is a literal-text check, not semantic
    inference (see module docstring).
    """
    groups: dict[str, list[KnowledgeNode]] = {}
    for node in nodes:
        if node.is_expired(now=now) or not _applies(node, task_tags):
            continue
        key = node.frontmatter.get("key")
        if not key:
            continue
        groups.setdefault(str(key), []).append(node)

    findings: list[ConflictFinding] = []
    for key, group in sorted(groups.items()):
        distinct_texts = {n.text.strip() for n in group}
        if len(distinct_texts) > 1:
            findings.append(
                ConflictFinding(
                    key=key,
                    node_ids=tuple(sorted(n.id for n in group)),
                    reason=f"{len(distinct_texts)} distinct constraint values under key {key!r}",
                )
            )
    return findings


def expertise_pack_to_context_items(
    pack: ExpertisePack,
    *,
    task_tags: set[str] | None = None,
    estimator: TokenEstimator | None = None,
    now: float | None = None,
) -> list[ContextItem]:
    """Materialise applicable, live, structurally-valid nodes into :class:`ContextItem` candidates.

    Expired nodes, (when *task_tags* is given) inapplicable nodes, and nodes
    missing the required ``key`` (flagged at load time as "not a valid
    constraint node") are excluded — pack sections enter context "only when
    relevant" (issue #776 acceptance criteria), never unconditionally.
    """
    return [
        node_to_context_item(node, source_kind=SOURCE_KIND, estimator=estimator)
        for node in pack.nodes
        if "key" in node.frontmatter and not node.is_expired(now=now) and _applies(node, task_tags)
    ]


__all__ = [
    "SOURCE_KIND",
    "VERSION_FILENAME",
    "ConflictFinding",
    "ExpertisePack",
    "detect_conflicts",
    "expertise_pack_to_context_items",
    "load_expertise_pack",
]

"""OKF bundle loader as a context source (issue #736).

OKF stores concepts as Markdown files with YAML frontmatter. This module
loads an OKF directory offline (no network access) and exposes its concepts
as selectable :class:`~contextweaver.types.ContextItem` candidates that flow
through contextweaver's existing candidate selection, budget, dedup,
sensitivity, and rendering pipeline unchanged.

``index.md`` (optional overview/bundle metadata) and ``log.md`` (optional
bundle history) are recognised but excluded from normal concept content by
default — see :class:`OkfBundle`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from contextweaver._utils import tokenize
from contextweaver.adapters._okf_io import (
    INDEX_FILENAME,
    LOG_FILENAME,
    KnowledgeNode,
    LoadDiagnostic,
    discover_concept_files,
    node_from_markdown,
    validate_links,
)
from contextweaver.adapters._okf_materialize import node_to_context_item, score_node
from contextweaver.exceptions import ConfigError
from contextweaver.protocols import TokenEstimator
from contextweaver.types import ContextItem

#: This adapter's provenance discriminator (see ``node_to_context_item``).
SOURCE_KIND = "okf_bundle"


@dataclass
class OkfBundle:
    """The result of loading one OKF directory.

    Attributes:
        nodes: Concept nodes, in deterministic (sorted-path) order.
        index: The parsed ``index.md`` node, if present — bundle overview
            metadata, not a selectable concept.
        log: The parsed ``log.md`` node, if present — bundle history, not a
            selectable concept.
        diagnostics: Every recoverable finding from the load (missing
            titles, invalid frontmatter, broken links, ...).
    """

    nodes: list[KnowledgeNode] = field(default_factory=list)
    index: KnowledgeNode | None = None
    log: KnowledgeNode | None = None
    diagnostics: list[LoadDiagnostic] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "index": self.index.to_dict() if self.index else None,
            "log": self.log.to_dict() if self.log else None,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
        }


def load_okf_bundle(
    path: str | Path, *, on_invalid: Literal["warn", "raise"] = "warn"
) -> OkfBundle:
    """Load an OKF bundle directory into an :class:`OkfBundle`.

    Args:
        path: Root directory of the OKF bundle.
        on_invalid: ``"warn"`` (default) keeps degraded nodes and collects
            diagnostics; ``"raise"`` converts the first diagnostic into a
            :class:`ConfigError` instead.

    Raises:
        ConfigError: If *path* is not a directory, or (``on_invalid="raise"``)
            a node failed to parse cleanly.
    """
    root = Path(path)
    if not root.is_dir():
        msg = f"load_okf_bundle: not a directory: {root}"
        raise ConfigError(msg)

    diagnostics: list[LoadDiagnostic] = []
    nodes: list[KnowledgeNode] = []
    for file_path in discover_concept_files(root):
        source_path = file_path.relative_to(root).as_posix()
        node, diagnostic = node_from_markdown(
            file_path.read_text(encoding="utf-8"), source_path=source_path
        )
        if diagnostic:
            if on_invalid == "raise":
                msg = f"load_okf_bundle: {diagnostic.path}: {diagnostic.message}"
                raise ConfigError(msg)
            diagnostics.append(diagnostic)
        nodes.append(node)

    index = _load_bundle_file(root, INDEX_FILENAME)
    log = _load_bundle_file(root, LOG_FILENAME)
    diagnostics.extend(validate_links(nodes))
    return OkfBundle(nodes, index=index, log=log, diagnostics=diagnostics)


def _load_bundle_file(root: Path, filename: str) -> KnowledgeNode | None:
    """Load a bundle-level file (``index.md`` / ``log.md``) if present."""
    file_path = root / filename
    if not file_path.is_file():
        return None
    node, _diagnostic = node_from_markdown(
        file_path.read_text(encoding="utf-8"), source_path=filename
    )
    return node


def okf_nodes_to_context_items(
    nodes: list[KnowledgeNode],
    *,
    estimator: TokenEstimator | None = None,
    now: float | None = None,
) -> list[ContextItem]:
    """Materialise *nodes* into :class:`ContextItem` candidates.

    Expired nodes (per :meth:`KnowledgeNode.is_expired`) are filtered out.
    """
    return [
        node_to_context_item(node, source_kind=SOURCE_KIND, estimator=estimator)
        for node in nodes
        if not node.is_expired(now=now)
    ]


def select_knowledge(
    nodes: list[KnowledgeNode],
    query: str,
    *,
    budget_tokens: int,
    estimator: TokenEstimator | None = None,
    now: float | None = None,
    max_nodes: int | None = None,
) -> list[ContextItem]:
    """Rank *nodes* by relevance to *query* and greedily pack under *budget_tokens*.

    Deterministic: ties break by node ID (mirrors
    :func:`contextweaver.context.memory_source.select_memory_for_phase`).
    """
    if budget_tokens <= 0:
        return []
    query_tokens = tokenize(query)
    live = [n for n in nodes if not n.is_expired(now=now)]
    ranked = sorted(live, key=lambda n: (-score_node(n, query_tokens), n.id))
    if max_nodes is not None:
        ranked = ranked[:max_nodes]

    packed: list[ContextItem] = []
    remaining = budget_tokens
    for node in ranked:
        item = node_to_context_item(node, source_kind=SOURCE_KIND, estimator=estimator)
        cost = max(1, int(item.token_estimate))
        if cost > remaining:
            continue
        packed.append(item)
        remaining -= cost
    return packed


__all__ = [
    "SOURCE_KIND",
    "OkfBundle",
    "load_okf_bundle",
    "okf_nodes_to_context_items",
    "select_knowledge",
]

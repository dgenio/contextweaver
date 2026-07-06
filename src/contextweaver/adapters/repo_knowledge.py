"""Repository-knowledge bundles as context sources (issue #763).

Narrows the OKF loader (:mod:`.okf`, issue #736) to generated repository
documentation: repo wikis, agent docs, architecture notes, module summaries,
invariants, test strategy, and contribution guidance. Reuses the same
Markdown-plus-frontmatter core with a plain-Markdown fallback (files with no
frontmatter still become candidates) and adds deterministic usage-tag
classification.

Usage tags are plain metadata strings (``"onboarding"``, ``"debugging"``,
...), **not** :class:`~contextweaver.types.Phase` values — extending the
fixed ``route``/``call``/``interpret``/``answer`` phase enum is the subject
of a separate open issue (#587) and is explicitly out of scope here.

Design constraint (#763): references from ``AGENTS.md``/``CLAUDE.md`` or any
node's ``links`` are never auto-followed — the loader only reads files under
the given root, so a documentation tree cannot force-load content outside
the bundle the caller pointed it at.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from contextweaver._utils import tokenize
from contextweaver.adapters._okf_io import KnowledgeNode, LoadDiagnostic, node_from_markdown
from contextweaver.adapters._okf_materialize import node_to_context_item, score_node
from contextweaver.exceptions import ConfigError
from contextweaver.protocols import TokenEstimator
from contextweaver.types import ContextItem

SOURCE_KIND = "repo_knowledge"

#: (usage tag, keywords) — first matching tag(s) win; deterministic, no ML.
_USAGE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("onboarding", ("quickstart", "getting started", "onboarding", "overview")),
    ("debugging", ("debug", "troubleshoot", "error", "logging")),
    ("refactor", ("refactor", "decompose", "restructure")),
    ("test-writing", ("test", "fixture", "coverage")),
    ("architecture-review", ("architecture", "invariant", "design", "module map")),
    ("release-prep", ("release", "changelog", "version", "publish")),
)

DEFAULT_MAX_FILES = 500
DEFAULT_MAX_TOTAL_BYTES = 5_000_000


@dataclass
class RepoKnowledgeBundle:
    """The result of loading a repository-knowledge tree.

    Attributes:
        nodes: Documentation nodes, deterministic (sorted-path) order.
        diagnostics: Every recoverable finding from the load.
        truncated: ``True`` if the ``max_files`` / ``max_total_bytes`` guardrail
            stopped the walk before covering every file under the root.
    """

    nodes: list[KnowledgeNode] = field(default_factory=list)
    diagnostics: list[LoadDiagnostic] = field(default_factory=list)
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "truncated": self.truncated,
        }


def classify_usage(node: KnowledgeNode) -> list[str]:
    """Return deterministic usage tags for *node* (e.g. ``["debugging"]``).

    Matches keywords against the node's type, title, and tags. A node may
    match zero, one, or several tags; matches are returned in
    :data:`_USAGE_KEYWORDS` order, never randomised.
    """
    haystack = " ".join([node.node_type, node.title, *node.tags]).lower()
    return [tag for tag, keywords in _USAGE_KEYWORDS if any(kw in haystack for kw in keywords)]


def load_repo_knowledge(
    path: str | Path,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    on_invalid: Literal["warn", "raise"] = "warn",
) -> RepoKnowledgeBundle:
    """Load a repository-knowledge tree, bounded by *max_files*/*max_total_bytes*.

    Every ``.md`` file under *path* is a candidate (frontmatter optional —
    plain Markdown falls back to filename-derived title/ID, per the shared
    permissive parser). ``index.md``/``log.md`` filenames carry no special
    meaning here (unlike :func:`contextweaver.adapters.okf.load_okf_bundle`);
    this is a documentation tree, not an OKF bundle proper.

    Raises:
        ConfigError: If *path* is not a directory, or (``on_invalid="raise"``)
            a node failed to parse cleanly.
    """
    root = Path(path)
    if not root.is_dir():
        msg = f"load_repo_knowledge: not a directory: {root}"
        raise ConfigError(msg)

    files = sorted(root.rglob("*.md"))
    truncated = len(files) > max_files
    files = files[:max_files]

    diagnostics: list[LoadDiagnostic] = []
    nodes: list[KnowledgeNode] = []
    total_bytes = 0
    for file_path in files:
        raw = file_path.read_bytes()
        total_bytes += len(raw)
        if total_bytes > max_total_bytes:
            truncated = True
            break
        source_path = file_path.relative_to(root).as_posix()
        node, diagnostic = node_from_markdown(
            raw.decode("utf-8", errors="replace"), source_path=source_path
        )
        if diagnostic:
            if on_invalid == "raise":
                msg = f"load_repo_knowledge: {diagnostic.path}: {diagnostic.message}"
                raise ConfigError(msg)
            diagnostics.append(diagnostic)
        nodes.append(node)

    return RepoKnowledgeBundle(nodes, diagnostics=diagnostics, truncated=truncated)


def repo_knowledge_nodes_to_context_items(
    nodes: list[KnowledgeNode],
    *,
    estimator: TokenEstimator | None = None,
    now: float | None = None,
) -> list[ContextItem]:
    """Materialise *nodes* into :class:`ContextItem` candidates.

    Each item's ``metadata["tags"]`` is extended with :func:`classify_usage`
    tags (deduplicated, order-preserving) so downstream selection can filter
    by intended use without a separate lookup pass.
    """
    items: list[ContextItem] = []
    for node in nodes:
        if node.is_expired(now=now):
            continue
        item = node_to_context_item(node, source_kind=SOURCE_KIND, estimator=estimator)
        usage_tags = classify_usage(node)
        if usage_tags:
            existing = item.metadata.get("tags", [])
            item.metadata["tags"] = existing + [t for t in usage_tags if t not in existing]
        items.append(item)
    return items


def select_repo_knowledge(
    nodes: list[KnowledgeNode],
    query: str,
    *,
    budget_tokens: int,
    usage_tag: str | None = None,
    estimator: TokenEstimator | None = None,
    now: float | None = None,
) -> list[ContextItem]:
    """Rank *nodes* by relevance to *query*, optionally filtered by *usage_tag*.

    Deterministic; ties break by node ID.
    """
    if budget_tokens <= 0:
        return []
    candidates = nodes
    if usage_tag is not None:
        candidates = [n for n in candidates if usage_tag in classify_usage(n)]
    query_tokens = tokenize(query)
    live = [n for n in candidates if not n.is_expired(now=now)]
    ranked = sorted(live, key=lambda n: (-score_node(n, query_tokens), n.id))

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
    "DEFAULT_MAX_FILES",
    "DEFAULT_MAX_TOTAL_BYTES",
    "SOURCE_KIND",
    "RepoKnowledgeBundle",
    "classify_usage",
    "load_repo_knowledge",
    "repo_knowledge_nodes_to_context_items",
    "select_repo_knowledge",
]

"""LessonWeaver lesson bundles as lifecycle-aware context sources (issue #767).

Extends the OKF loading core (:mod:`._okf_io`, issue #736) with
lifecycle-aware selection: a lesson's ``status``, ``scope``, ``confidence``,
and ``expires_at`` govern whether it is eligible to enter model context, not
just how it ranks.

Non-goals (issue #767): this module does not reimplement lesson review or
promotion, does not make lessons authoritative without lifecycle metadata,
and never auto-injects lessons — callers explicitly call
:func:`select_lessons` / :func:`lesson_nodes_to_context_items`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from contextweaver._utils import tokenize
from contextweaver.adapters._okf_io import (
    KnowledgeNode,
    LoadDiagnostic,
    discover_concept_files,
    node_from_markdown,
    read_markdown_text,
    validate_links,
)
from contextweaver.adapters._okf_materialize import node_to_context_item, score_node
from contextweaver.exceptions import ConfigError
from contextweaver.protocols import TokenEstimator
from contextweaver.types import ContextItem

logger = logging.getLogger("contextweaver.adapters")

SOURCE_KIND = "lesson_bundle"

#: Statuses excluded from selection unless explicitly requested.
DEFAULT_EXCLUDED_STATUSES = ("rejected", "deprecated")
#: Statuses excluded from selection *unless* ``include_candidates=True``.
CANDIDATE_STATUS = "candidate"


@dataclass(frozen=True)
class LessonSelectionPolicy:
    """Lifecycle-eligibility rules applied before ranking (issue #767).

    Attributes:
        excluded_statuses: Statuses never eligible, regardless of other
            settings. Defaults to ``("rejected", "deprecated")``.
        include_candidates: When ``False`` (default), lessons with
            ``status == "candidate"`` are excluded unless explicitly opted
            in.
        preferred_scope: When set, lessons whose ``scope`` matches rank
            above lessons that don't (does not exclude non-matching scope).
    """

    excluded_statuses: tuple[str, ...] = DEFAULT_EXCLUDED_STATUSES
    include_candidates: bool = False
    preferred_scope: str | None = None


@dataclass(frozen=True)
class LessonExclusion:
    """A lesson that was excluded from selection, and why."""

    node_id: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        """Serialise to a JSON-compatible dict."""
        return {"node_id": self.node_id, "reason": self.reason}


def load_lesson_bundle(
    path: str | Path, *, on_invalid: Literal["warn", "raise"] = "warn"
) -> tuple[list[KnowledgeNode], list[LoadDiagnostic]]:
    """Load a LessonWeaver-exported bundle directory.

    Returns:
        A ``(nodes, diagnostics)`` tuple. All nodes are returned regardless
        of lifecycle status — filtering happens in :func:`select_lessons`,
        keeping the load step and the eligibility policy independently
        testable.

    Raises:
        ConfigError: If *path* is not a directory, or (``on_invalid="raise"``)
            a node failed to parse cleanly.
    """
    root = Path(path)
    if not root.is_dir():
        msg = f"load_lesson_bundle: not a directory: {root}"
        raise ConfigError(msg)

    diagnostics: list[LoadDiagnostic] = []
    nodes: list[KnowledgeNode] = []
    for file_path in discover_concept_files(root):
        source_path = file_path.relative_to(root).as_posix()
        node, diagnostic = node_from_markdown(
            read_markdown_text(file_path), source_path=source_path
        )
        if diagnostic:
            if on_invalid == "raise":
                msg = f"load_lesson_bundle: {diagnostic.path}: {diagnostic.message}"
                raise ConfigError(msg)
            diagnostics.append(diagnostic)
        nodes.append(node)

    diagnostics.extend(validate_links(nodes))
    return nodes, diagnostics


def eligible_lessons(
    nodes: list[KnowledgeNode],
    policy: LessonSelectionPolicy,
    *,
    now: float | None = None,
) -> tuple[list[KnowledgeNode], list[LessonExclusion]]:
    """Split *nodes* into ``(eligible, excluded)`` per *policy*.

    Exclusion reasons are explicit strings (``"status:rejected"``,
    ``"expired"``) so callers can surface lifecycle/staleness diagnostics
    (issue #767 acceptance criteria) without re-deriving them.
    """
    eligible: list[KnowledgeNode] = []
    excluded: list[LessonExclusion] = []
    for node in nodes:
        if node.status in policy.excluded_statuses:
            excluded.append(LessonExclusion(node.id, f"status:{node.status}"))
            continue
        if node.status == CANDIDATE_STATUS and not policy.include_candidates:
            excluded.append(LessonExclusion(node.id, "status:candidate (not opted in)"))
            continue
        if node.is_expired(now=now):
            excluded.append(LessonExclusion(node.id, "expired"))
            continue
        eligible.append(node)
    logger.debug("lessons.eligible_lessons: eligible=%d, excluded=%d", len(eligible), len(excluded))
    return eligible, excluded


def lesson_nodes_to_context_items(
    nodes: list[KnowledgeNode],
    *,
    estimator: TokenEstimator | None = None,
) -> list[ContextItem]:
    """Materialise already-filtered *nodes* into :class:`ContextItem` candidates.

    Provenance (stamped by the shared materialiser under
    ``metadata["_contextweaver"]["knowledge_source"]``) already carries
    ``status``, ``scope``, and ``confidence`` so lifecycle metadata survives
    into rendered context (issue #767 acceptance criteria).
    """
    return [
        node_to_context_item(node, source_kind=SOURCE_KIND, estimator=estimator) for node in nodes
    ]


def select_lessons(
    nodes: list[KnowledgeNode],
    query: str,
    *,
    budget_tokens: int,
    policy: LessonSelectionPolicy | None = None,
    estimator: TokenEstimator | None = None,
    now: float | None = None,
) -> tuple[list[ContextItem], list[LessonExclusion]]:
    """Filter by *policy*, rank by relevance, and greedily pack under *budget_tokens*.

    ``policy`` defaults to :class:`LessonSelectionPolicy`'s own defaults
    (exclude rejected/deprecated, exclude unreviewed candidates) when
    omitted.

    Returns:
        A ``(items, exclusions)`` tuple — deterministic; ties break by node
        ID.
    """
    resolved_policy = policy if policy is not None else LessonSelectionPolicy()
    eligible, excluded = eligible_lessons(nodes, resolved_policy, now=now)
    if budget_tokens <= 0:
        return [], excluded

    query_tokens = tokenize(query)

    def _sort_key(node: KnowledgeNode) -> tuple[int, float, str]:
        preferred = resolved_policy.preferred_scope
        scope_bonus = 0 if preferred and node.scope == preferred else 1
        return (scope_bonus, -score_node(node, query_tokens), node.id)

    ranked = sorted(eligible, key=_sort_key)

    packed: list[ContextItem] = []
    remaining = budget_tokens
    for node in ranked:
        item = node_to_context_item(node, source_kind=SOURCE_KIND, estimator=estimator)
        cost = max(1, int(item.token_estimate))
        if cost > remaining:
            continue
        packed.append(item)
        remaining -= cost
    return packed, excluded


__all__ = [
    "CANDIDATE_STATUS",
    "DEFAULT_EXCLUDED_STATUSES",
    "SOURCE_KIND",
    "LessonExclusion",
    "LessonSelectionPolicy",
    "eligible_lessons",
    "lesson_nodes_to_context_items",
    "load_lesson_bundle",
    "select_lessons",
]

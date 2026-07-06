"""Private materialisation + scoring helpers shared by the knowledge-source adapters.

Split out of :mod:`._okf_io` (issues #736/#763/#767/#776) to keep both
modules within the ≤300-line convention. Turns a :class:`.KnowledgeNode`
into a :class:`~contextweaver.types.ContextItem` and provides a deterministic
relevance score, so :mod:`.okf`, :mod:`.repo_knowledge`, :mod:`.lessons`, and
:mod:`.expertise_pack` share one implementation instead of four copies.
"""

from __future__ import annotations

from typing import Any

from contextweaver._utils import jaccard, tokenize
from contextweaver.adapters._okf_io import KnowledgeNode
from contextweaver.protocols import TokenEstimator
from contextweaver.tokens import heuristic_counter
from contextweaver.types import ContextItem, ItemKind

_DEFAULT_COUNTER: TokenEstimator = heuristic_counter()


def estimate_cost(text: str, estimator: TokenEstimator | None) -> int:
    """Return a positive token estimate for *text*.

    Falls back to the shared canonical script-aware heuristic counter
    (mirrors :mod:`contextweaver.context.memory_source`) rather than an
    inline ``len // 4`` literal when no estimator is supplied.
    """
    counter = estimator if estimator is not None else _DEFAULT_COUNTER
    return max(1, int(counter.estimate(text)))


def node_to_context_item(
    node: KnowledgeNode,
    *,
    source_kind: str,
    estimator: TokenEstimator | None = None,
    kind: ItemKind = ItemKind.doc_snippet,
) -> ContextItem:
    """Materialise *node* into a :class:`ContextItem`.

    Provenance and lifecycle metadata are stamped under the reserved
    ``_contextweaver`` namespace, mirroring
    :func:`contextweaver.context.memory_source.memory_entries_to_context_items`
    — the sensitivity, scoring, dedup, and rendering stages consume the
    result unchanged; no pipeline stage is modified.

    Args:
        node: The parsed node.
        source_kind: Discriminator stamped into the provenance block, e.g.
            ``"okf_bundle"``, ``"repo_knowledge"``, ``"lesson_bundle"``,
            ``"expertise_pack"``.
        estimator: Optional token estimator.
        kind: Item kind. Defaults to ``doc_snippet`` — no dedicated
            ``ItemKind`` is introduced for this adapter family.
    """
    metadata: dict[str, Any] = {}
    if node.tags:
        metadata["tags"] = list(node.tags)
    metadata["_contextweaver"] = {
        "knowledge_source": {
            "kind": source_kind,
            "id": node.id,
            "node_type": node.node_type,
            "source_path": node.source_path,
            "scope": node.scope,
            "status": node.status,
            "confidence": node.confidence,
            "timestamp": node.timestamp,
        }
    }
    if node.frontmatter:
        metadata["frontmatter"] = dict(node.frontmatter)
    return ContextItem(
        id=f"{source_kind}:{node.id}",
        kind=kind,
        text=node.text,
        token_estimate=estimate_cost(node.text, estimator),
        sensitivity=node.sensitivity,
        metadata=metadata,
    )


def score_node(node: KnowledgeNode, query_tokens: set[str]) -> float:
    """Return a deterministic relevance score for *node*.

    Uses the single-source-of-truth text-similarity primitives
    (:func:`contextweaver._utils.tokenize` / :func:`~contextweaver._utils.jaccard`)
    per the repo's "do not duplicate" convention, plus the node's own
    confidence — mirrors
    :func:`contextweaver.context.memory_fixture._entry_score`.
    """
    node_tokens = tokenize(node.text) | tokenize(node.title) | tokenize(" ".join(node.tags))
    overlap = jaccard(query_tokens, node_tokens) if query_tokens else 0.0
    confidence = max(0.0, min(1.0, node.confidence))
    return overlap + confidence


__all__ = ["estimate_cost", "node_to_context_item", "score_node"]

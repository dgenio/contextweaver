"""Automatic labeler for :class:`~contextweaver.types.SelectableItem` objects.

The labeler assigns a short descriptive label and a routing hint to a group
of items based on their names, descriptions, and tags.  Used by the tree
builder to label intermediate nodes in the choice graph.

The single-item :meth:`KeywordLabeler.label` satisfies the
:class:`~contextweaver.protocols.Labeler` protocol.
"""

from __future__ import annotations

from collections import Counter

from contextweaver._utils import tokenize
from contextweaver.types import SelectableItem

# ---------------------------------------------------------------------------
# Built-in category vocabulary
# ---------------------------------------------------------------------------

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "data": ["read", "write", "fetch", "query", "database", "file", "storage", "data"],
    "compute": ["calculate", "compute", "math", "transform", "process", "run", "execute"],
    "communication": ["send", "notify", "email", "message", "post", "webhook", "slack"],
    "search": ["search", "find", "lookup", "retrieve", "index", "browse"],
    "auth": ["auth", "login", "token", "permission", "credential", "oauth", "key"],
    "monitoring": ["monitor", "alert", "metric", "log", "trace", "health", "status"],
    "ml": ["model", "predict", "infer", "embed", "classify", "train", "llm", "ai"],
    "agent": ["agent", "plan", "delegate", "skill", "orchestrate", "route"],
}


class KeywordLabeler:
    """Assign labels and routing hints to items or item groups.

    Category is determined by which vocabulary set has the highest overlap
    with the item's combined token set (name + description + tags).  Returns
    ``"general"`` when no category matches with non-zero overlap.

    For groups of items, :meth:`label_group` produces a composite label
    built from the top-K most frequent tokens, optionally prepended by the
    dominant namespace when more than *namespace_threshold* of the items
    share it.

    Confidence is expressed as a human-readable string:
    ``"high"`` (overlap >= 0.5), ``"medium"`` (>= 0.2), ``"low"`` (> 0),
    ``"none"`` (no match).
    """

    def __init__(
        self,
        top_k: int = 3,
        namespace_threshold: float = 0.6,
    ) -> None:
        self._top_k = top_k
        self._namespace_threshold = namespace_threshold

    # ------------------------------------------------------------------
    # Protocol-compatible single-item label
    # ------------------------------------------------------------------

    def label(self, item: SelectableItem) -> tuple[str, str]:
        """Return ``(category, confidence)`` for *item*.

        Satisfies the :class:`~contextweaver.protocols.Labeler` protocol.

        Args:
            item: The item to label.

        Returns:
            A 2-tuple ``(category_str, confidence_str)``.
        """
        combined = f"{item.name} {item.description} {' '.join(item.tags)}"
        tokens = tokenize(combined)
        if not tokens:
            return ("general", "none")

        best_cat = "general"
        best_score = 0.0

        for category, keywords in sorted(_CATEGORY_KEYWORDS.items()):
            overlap = len(tokens & set(keywords))
            score = overlap / len(tokens)
            if score > best_score:
                best_score = score
                best_cat = category

        if best_score >= 0.5:
            confidence = "high"
        elif best_score >= 0.2:
            confidence = "medium"
        elif best_score > 0.0:
            confidence = "low"
        else:
            confidence = "none"
            best_cat = "general"

        return (best_cat, confidence)

    # ------------------------------------------------------------------
    # Group labeling for tree nodes
    # ------------------------------------------------------------------

    def label_group(self, items: list[SelectableItem]) -> tuple[str, str]:
        """Return ``(label, routing_hint)`` for a group of items.

        The label is composed of the top-K most frequent tokens across all
        items.  If more than *namespace_threshold* of the items share a
        single namespace, the namespace is prepended to the label.

        Args:
            items: The items in the group.

        Returns:
            A 2-tuple ``(label, routing_hint)`` where *routing_hint* is a
            human-readable sentence like ``"Tools related to <label>"``.
        """
        if not items:
            return ("general", "Tools related to general")

        # Collect token frequencies across all items
        freq: Counter[str] = Counter()
        for item in items:
            combined = f"{item.name} {item.description} {' '.join(item.tags)}"
            tokens = tokenize(combined)
            freq.update(tokens)

        # Top-K frequent tokens (sorted by freq desc, then alphabetical)
        top_tokens = [
            tok
            for tok, _ in sorted(
                freq.items(), key=lambda x: (-x[1], x[0])
            )[: self._top_k]
        ]
        label = " ".join(top_tokens) if top_tokens else "general"

        # Prepend dominant namespace if threshold is met
        ns_counts: Counter[str] = Counter()
        for item in items:
            if item.namespace:
                ns_counts[item.namespace] += 1
        if ns_counts:
            dominant_ns, dominant_count = ns_counts.most_common(1)[0]
            if dominant_count / len(items) >= self._namespace_threshold:
                label = f"{dominant_ns}: {label}"

        routing_hint = f"Tools related to {label}"
        return (label, routing_hint)

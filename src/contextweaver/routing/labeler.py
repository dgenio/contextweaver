"""Automatic labeler for :class:`~contextweaver.types.SelectableItem` objects.

The labeler assigns a category label and a confidence string to each item
based on its name, description, and tags.  Used by the tree builder to
cluster items into namespaces / capability groups.
"""

from __future__ import annotations

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
    """Assign a category to a :class:`~contextweaver.types.SelectableItem` using keyword matching.

    Category is determined by which vocabulary set has the highest overlap with
    the item's combined token set (name + description + tags).  Returns
    ``"general"`` when no category matches with non-zero overlap.

    Confidence is expressed as a human-readable string:
    ``"high"`` (overlap ≥ 0.5), ``"medium"`` (≥ 0.2), ``"low"`` (> 0),
    ``"none"`` (no match).
    """

    def label(self, item: SelectableItem) -> tuple[str, str]:
        """Return ``(category, confidence)`` for *item*.

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

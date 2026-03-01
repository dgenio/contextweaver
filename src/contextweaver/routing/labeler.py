"""Automatic labeler for groups of SelectableItems.

The labeler assigns a category label and routing hint to a group of items
based on frequent tokens and namespace prevalence.
"""

from __future__ import annotations

from collections import Counter

from contextweaver._utils import tokenize
from contextweaver.types import SelectableItem


class KeywordLabeler:
    """Default Labeler. Top-K frequent tokens (stopword-filtered).

    Prepends dominant namespace if >threshold of items share it.
    routing_hint = "Tools related to {label}".
    """

    def __init__(self, top_k: int = 3, namespace_threshold: float = 0.6) -> None:
        self._top_k = top_k
        self._ns_threshold = namespace_threshold

    def label(self, items: list[SelectableItem]) -> tuple[str, str]:
        """Return (label, routing_hint) for a group of items."""
        if not items:
            return ("empty", "No tools available")

        # Check namespace dominance
        ns_counts: Counter[str] = Counter()
        for item in items:
            if item.namespace:
                ns_counts[item.namespace] += 1
        dominant_ns = ""
        if ns_counts:
            top_ns, top_count = ns_counts.most_common(1)[0]
            if top_count / len(items) >= self._ns_threshold:
                dominant_ns = top_ns

        # Collect all tokens from names + descriptions + tags
        all_tokens: Counter[str] = Counter()
        for item in items:
            text = f"{item.name} {item.description} {' '.join(item.tags)}"
            for token in tokenize(text):
                all_tokens[token] += 1

        # Get top-k tokens
        top_tokens = [t for t, _ in all_tokens.most_common(self._top_k)]

        if dominant_ns:
            label = f"{dominant_ns}: {', '.join(top_tokens)}" if top_tokens else dominant_ns
        elif top_tokens:
            label = ", ".join(top_tokens)
        else:
            label = "miscellaneous"

        hint = f"Tools related to {label}"
        return (label, hint)

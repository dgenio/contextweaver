"""Rule-based summarisation for contextweaver.

Provides a simple rule engine that maps media-type patterns and metadata tags
to summarisation strategies.  Also includes :class:`RuleBasedSummarizer`, the
default :class:`~contextweaver.protocols.Summarizer` implementation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SummarizationRule:
    """A single rule mapping a condition to a summariser name.

    Attributes:
        media_type_prefix: Match if the artifact's media_type starts with this
            string.  Empty string matches everything.
        required_tags: All listed tags must be present in the metadata's
            ``tags`` list for the rule to fire.
        summarizer_name: Logical name of the summariser to use when this rule
            fires.  Resolved at runtime by the summarisation dispatcher.
        priority: Higher priority rules are evaluated first.
    """

    media_type_prefix: str = ""
    required_tags: list[str] = field(default_factory=list)
    summarizer_name: str = "default"
    priority: int = 0

    def matches(self, media_type: str, metadata: dict[str, Any]) -> bool:
        """Return ``True`` if this rule fires for the given artifact context.

        Args:
            media_type: The MIME type of the artifact.
            metadata: Arbitrary metadata dict associated with the artifact.

        Returns:
            ``True`` when all conditions are satisfied.
        """
        if self.media_type_prefix and not media_type.startswith(self.media_type_prefix):
            return False
        tags: list[str] = metadata.get("tags", [])
        return all(t in tags for t in self.required_tags)


class RuleEngine:
    """Dispatch summarisation by evaluating a ranked list of :class:`SummarizationRule` objects.

    Rules are sorted by descending priority; the first match wins.
    """

    def __init__(self, rules: list[SummarizationRule] | None = None) -> None:
        self._rules: list[SummarizationRule] = sorted(
            rules or [], key=lambda r: r.priority, reverse=True
        )

    def add_rule(self, rule: SummarizationRule) -> None:
        """Append *rule* and re-sort the rule list.

        Args:
            rule: The rule to add.
        """
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority, reverse=True)

    def resolve(self, media_type: str, metadata: dict[str, Any]) -> str:
        """Return the summariser name for the first matching rule.

        Args:
            media_type: MIME type of the artifact.
            metadata: Metadata dict.

        Returns:
            The ``summarizer_name`` of the first matching rule, or ``"default"``
            if no rule matches.
        """
        for rule in self._rules:
            if rule.matches(media_type, metadata):
                return rule.summarizer_name
        return "default"

    def all_rules(self) -> list[SummarizationRule]:
        """Return a copy of the rule list in priority order."""
        return list(self._rules)


# ---------------------------------------------------------------------------
# Key-line keywords for prioritised extraction
# ---------------------------------------------------------------------------

_KEY_LINE_RE = re.compile(
    r"\b(error|success|failed|failure|total|count|status|result"
    r"|warning|exception|ok|pass|deny|allow)\b",
    re.IGNORECASE,
)


class RuleBasedSummarizer:
    """Default :class:`~contextweaver.protocols.Summarizer` implementation.

    Strategies:

    - **Plain text**: head + tail + ``[...truncated...]`` marker.
    - **JSON**: top-level keys + structure overview.
    - **Key lines**: prioritise lines with numbers, dates, and status keywords
      (error, success, failed, total, count, etc.).

    The output is bounded to approximately *max_chars* characters.

    # FUTURE: LLM labeler/extractor for richer summaries.
    """

    def __init__(self, max_chars: int = 600) -> None:
        self._max_chars = max_chars

    def summarize(self, raw: str, metadata: dict[str, Any]) -> str:
        """Produce a concise summary of *raw*.

        Args:
            raw: The raw tool output string.
            metadata: Optional metadata (e.g. ``media_type``).

        Returns:
            A human/LLM-readable summary bounded by *max_chars*.
        """
        media = str(metadata.get("media_type", ""))
        if "json" in media or self._looks_like_json(raw):
            return self._summarize_json(raw)
        return self._summarize_text(raw)

    # -- internal strategies ------------------------------------------------

    @staticmethod
    def _looks_like_json(text: str) -> bool:
        stripped = text.strip()
        return (
            stripped.startswith("{") and stripped.endswith("}")
        ) or (stripped.startswith("[") and stripped.endswith("]"))

    def _summarize_json(self, raw: str) -> str:
        """Summarise a JSON string: top-level keys + structure."""
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return self._summarize_text(raw)

        if isinstance(obj, dict):
            keys = sorted(obj.keys())
            overview = ", ".join(keys[:20])
            extra = (
                f" (+{len(keys) - 20} more)"
                if len(keys) > 20
                else ""
            )
            return f"JSON object with keys: [{overview}{extra}]"

        if isinstance(obj, list):
            length = len(obj)
            if length > 0 and isinstance(obj[0], dict):
                cols = sorted(obj[0].keys())
                return (
                    f"JSON array: {length} objects, "
                    f"columns: [{', '.join(cols[:15])}]"
                )
            return f"JSON array: {length} items"

        return f"JSON value: {str(obj)[:self._max_chars]}"

    def _summarize_text(self, raw: str) -> str:
        """Summarise plain text: key lines, then head + tail."""
        lines = raw.splitlines()
        if not lines:
            return "(empty)"

        # Collect key lines (containing status keywords or numbers)
        key_lines: list[str] = []
        for line in lines:
            if _KEY_LINE_RE.search(line):
                key_lines.append(line.strip())

        budget = self._max_chars
        parts: list[str] = []

        if key_lines:
            kl_text = "\n".join(key_lines[:10])
            if len(kl_text) > budget // 2:
                kl_text = kl_text[: budget // 2]
            parts.append(kl_text)
            budget -= len(kl_text)

        # Head + tail
        if len(lines) <= 8 or budget <= 0:
            body = "\n".join(lines[:8])
            if len(body) > budget and budget > 0:
                body = body[:budget]
            if budget > 0:
                parts.append(body)
        else:
            half = max(budget // 2, 50)
            head = "\n".join(lines[:4])[:half]
            tail = "\n".join(lines[-3:])[:half]
            parts.append(head)
            parts.append("[...truncated...]")
            parts.append(tail)

        return "\n".join(parts).strip()[: self._max_chars]
